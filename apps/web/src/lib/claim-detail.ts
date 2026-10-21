// Claim detail rules: which reviewer actions apply, what the trace says about each photo, and what the decision
// cites. The action rules mirror apps/api claims/review.py and the dev console's guidance; the API stays the
// authority (a 409 explains a refusal).
import { ClaimAction, type ClaimDetail, type Trace, type TraceStep } from "@/lib/api-types";
import type { ApiProblem } from "@/lib/api/problem";

/** review.py REVIEWABLE + DIRECT_REVIEWABLE: override, request info, and mark fraud apply here. */
export const ACTIONABLE_STATUSES = ["flagged", "under_review", "capped", "needs_info", "approved", "denied"] as const;
/** Statuses a person must act on (trace.md: key the actions on the claim status, not only the run). */
export const NEEDS_REVIEWER = ["flagged", "under_review", "capped"] as const;
const WAITING_RUN = ["waiting_human", "capped", "failed"];

export type ActionAvailability = {
  /** resume_override approve; not offered when the claim already holds that decision. */
  approve: boolean;
  /** resume_override deny; not offered on a claim that is already denied. */
  deny: boolean;
  requestInfo: boolean;
  markFraud: boolean;
  markDelivered: boolean;
  /** Why nothing applies, in plain words; null when at least one action applies. */
  blocked: string | null;
  /** The action resumes the paused agent run, so the worker applies it and the status changes a moment later. */
  appliedByWorker: boolean;
};

const includes = (list: readonly string[], v: string) => list.includes(v);

export function actionAvailability(c: Pick<ClaimDetail, "status" | "remedy" | "run">): ActionAvailability {
  const none = { approve: false, deny: false, requestInfo: false, markFraud: false, markDelivered: false, appliedByWorker: false };
  if (c.run && c.run.graph === "adjudicate" && c.run.status === "running") {
    return { ...none, blocked: "The claim is being adjudicated. Actions open when the run finishes." };
  }
  if (!includes(ACTIONABLE_STATUSES, c.status)) {
    const blocked: Record<string, string> = {
      draft: "The claimant has not submitted this claim yet.",
      submitted: "No decision yet. The claim waits for the adjudication worker.",
      paid: "The refund was delivered. There is nothing left to review.",
      closed: "The remedy was delivered. There is nothing left to review.",
    };
    return { ...none, blocked: blocked[c.status] ?? `Status "${c.status}" is not recognized by this console; refresh, or check the trace.` };
  }
  const deliverable = c.status === "approved" && (c.remedy === "repair" || c.remedy === "replacement");
  const appliedByWorker =
    !!c.run && c.run.graph === "adjudicate" && WAITING_RUN.includes(c.run.status) && includes(NEEDS_REVIEWER, c.status);
  return {
    approve: c.status !== "approved",
    deny: c.status !== "denied",
    requestInfo: true,
    markFraud: true,
    markDelivered: deliverable,
    blocked: null,
    appliedByWorker,
  };
}

export function needsReviewer(status: string): boolean {
  return includes(NEEDS_REVIEWER, status);
}

/** Decision citations: tool result ids (T1, T2, ...) point at trace steps; rule ids (R:...) at the program rules. */
export function splitCitations(cited: readonly string[]): { results: string[]; rules: string[] } {
  const results = cited.filter((c) => /^T\d+$/.test(c));
  return { results, rules: cited.filter((c) => !results.includes(c)) };
}

export function resultId(step: Pick<TraceStep, "result">): string | null {
  const id = step.result?.result_id;
  return typeof id === "string" ? id : null;
}

const PHOTO_TOOLS: Record<string, readonly string[]> = {
  label: ["read_label_photo"],
  destruction: ["check_destruction_photo", "check_destruction_presence"],
  receipt: ["check_purchase"],
};

export type Reading = { tool: string; idx: number; resultId: string | null; text: string; problem: boolean };

const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : null);
const str = (v: unknown) => (typeof v === "string" && v ? v : null);

/** One plain sentence for a tool result; unknown shapes fall back to "see the trace" rather than guessing. */
export function describeResult(tool: string, r: Record<string, unknown> | null): { text: string; problem: boolean } {
  if (!r) return { text: "No result recorded.", problem: true };
  if (str(r.error)) return { text: `The check failed: ${r.error as string}`, problem: true };
  if (r.capped !== undefined) return { text: `Not run: the run stopped at a cap (${String(r.capped)}).`, problem: true };
  if (tool === "read_label_photo") {
    if (r.legible === false) return { text: "The label could not be read.", problem: true };
    const parts = [
      `model ${str(r.model_text) ?? "not visible"}`,
      `serial ${str(r.serial_text) ?? "not visible"}`,
      `date code ${str(r.date_code_text) ?? "not visible"}`,
    ];
    const conf = pct(r.confidence);
    return { text: `Label read: ${parts.join(", ")}${conf ? ` (confidence ${conf})` : ""}.`, problem: !str(r.model_text) };
  }
  if (tool === "check_destruction_photo" || tool === "check_destruction_presence") {
    if (r.verdict === "not_required") return { text: "This remedy does not require a destruction photo.", problem: false };
    if (r.verdict === "missing") return { text: `No usable destruction photo${str(r.reason) ? `: ${r.reason as string}` : "."}`, problem: true };
    if (r.verdict === "present") return { text: "A destruction photo is present. The rules-only checker does not check what it shows.", problem: false };
    if (r.verdict === "checked") {
      const conf = pct(r.confidence);
      const seen = str(r.observed) ? ` ${r.observed as string}` : "";
      return r.complies === true
        ? { text: `The instructed destruction is visible.${seen}${conf ? ` (confidence ${conf})` : ""}`, problem: false }
        : { text: `The instructed destruction is not visible.${seen}${conf ? ` (confidence ${conf})` : ""}`, problem: true };
    }
  }
  if (tool === "check_model" || tool === "check_serial" || tool === "check_date_code") {
    const what = { check_model: "Model", check_serial: "Serial number", check_date_code: "Date code" }[tool];
    const rule = str(r.cited_rule) ? ` (${r.cited_rule as string})` : "";
    const verdict: Record<string, [string, boolean]> = {
      match: [`${what} is part of the recall${rule}.`, false],
      in_range: [`${what} is inside the recalled range${rule}.`, false],
      mismatch: [`${what} is not part of the recall.`, true],
      out_of_range: [`${what} is outside the recalled range.`, true],
      ambiguous: [`${what} could match more than one rule.`, true],
      not_provided: [`No ${what.toLowerCase()} was given.`, true],
      unparsed: [`${what} could not be read.`, true],
      skipped: [`Not checked: the recall lists no ${what.toLowerCase()} rule.`, false],
    };
    const v = verdict[String(r.verdict)];
    if (v) return { text: v[0], problem: v[1] };
  }
  if (tool === "check_duplicates") {
    const n = Array.isArray(r.hits) ? r.hits.length : 0;
    return n ? { text: `${n} possible duplicate${n === 1 ? "" : "s"} found.`, problem: true } : { text: "No duplicates found.", problem: false };
  }
  if (tool === "retrieve_policy") {
    const n = Array.isArray(r.hits) ? r.hits.length : 0;
    return { text: `${n} policy passage${n === 1 ? "" : "s"} retrieved.`, problem: false };
  }
  if (tool === "decide" || tool === "decide_once") {
    if (r.accepted === true) return { text: "Decision accepted.", problem: false };
    if (r.accepted === false) return { text: "Decision rejected by validation.", problem: true };
  }
  if (tool === "check_purchase") {
    const receipt = { present: "receipt present", missing: "receipt missing", not_required: "no receipt required" }[String(r.receipt)];
    const retailer = { listed: "retailer is on the sold-at list", not_listed: "retailer is not on the sold-at list", unknown: "retailer unknown", skipped: "retailer not checked" }[String(r.retailer)];
    const window = { inside: "bought inside the recall window", before: "bought before the recall window", after: "bought after the recall window", unknown: "purchase date unknown", skipped: "purchase date not checked" }[String(r.window)];
    const parts = [receipt, retailer, window].filter(Boolean);
    if (parts.length) {
      return { text: `${parts.join("; ")}.`.replace(/^./, (c) => c.toUpperCase()), problem: r.receipt === "missing" || r.retailer === "not_listed" || r.window === "before" || r.window === "after" };
    }
  }
  return { text: "See the trace for this result.", problem: false };
}

/** The latest reading of each photo kind in the claim's latest run. */
export function readingsByPhotoKind(trace: Pick<Trace, "steps"> | null): Record<string, Reading> {
  const out: Record<string, Reading> = {};
  if (!trace) return out;
  for (const [kind, tools] of Object.entries(PHOTO_TOOLS)) {
    const step = [...trace.steps].reverse().find((s) => s.tool !== null && tools.includes(s.tool));
    if (step && step.tool) out[kind] = { tool: step.tool, idx: step.idx, resultId: resultId(step), ...describeResult(step.tool, step.result) };
  }
  return out;
}

export type DuplicateHit = { kind: string; otherClaimId: string; detail: string };

/** check_duplicates hits from the latest run (fraud indicators next to the Neo4j clusters). */
export function duplicateHits(trace: Pick<Trace, "steps"> | null): DuplicateHit[] {
  if (!trace) return [];
  const step = [...trace.steps].reverse().find((s) => s.tool === "check_duplicates");
  const hits = step?.result?.hits;
  if (!Array.isArray(hits)) return [];
  return hits.flatMap((h) => {
    if (typeof h !== "object" || h === null) return [];
    const r = h as Record<string, unknown>;
    return typeof r.other_claim_id === "string" ? [{ kind: String(r.kind ?? "duplicate"), otherClaimId: r.other_claim_id, detail: String(r.detail ?? "") }] : [];
  });
}

export const CLUSTER_LABEL: Record<string, string> = {
  same_photo: "Same photo as another claim",
  same_address: "Same home address as another claim",
  same_payout: "Same payout destination as another claim",
  same_serial: "Same serial number as another claim",
  same_mailing: "Same mailing address as another claim",
};

export function clusterLabel(kind: string): string {
  return Object.hasOwn(CLUSTER_LABEL, kind) ? CLUSTER_LABEL[kind]! : `Linked to other claims (${kind})`;
}

/** Only an API photo path is ever rendered, and only through the same-origin proxy. */
export function photoSrc(url: string, claimId: string): string | null {
  return url.startsWith(`/v1/claims/${claimId}/photos/`) && !url.includes("..") ? `/api${url}` : null;
}

/** Poll the claim while its run is in progress, or until `awaitingWorkerUntil` (a queued review, or a first run). */
export function claimPollMs(c: Pick<ClaimDetail, "run"> | undefined, awaitingWorkerUntil: number, now: number): number | false {
  if (c?.run?.status === "running") return 2_000;
  return now < awaitingWorkerUntil ? 2_000 : false;
}

/** trace.md: poll every 1 to 2 s while the run is running, then stop. */
export function tracePollMs(t: Pick<Trace, "run"> | undefined | null): number | false {
  return t?.run.status === "running" ? 1_500 : false;
}

/** How long the claim page keeps polling after a review the worker must apply. */
export const REVIEW_WAIT_MS = 60_000;
/** How long the claim and trace pages keep checking for the worker's run on a claim that has none yet. */
export const FIRST_RUN_WATCH_MS = 120_000;
/** The interval of both waits (2 to 3 s). */
export const WAIT_POLL_MS = 2_000;

/**
 * The claim waits for the worker: submitted (no decision yet, including a claim reopened after its decision), a run
 * in progress, or no run or trace loaded yet. A draft waits for the claimant, not the worker. Once a run exists and
 * is not running, and the claim is past submitted, there is nothing left to wait for.
 */
export function waitsForWorker(c: Pick<ClaimDetail, "status" | "run">, trace: Pick<Trace, "run"> | null | undefined): boolean {
  if (c.status === "draft") return false;
  if (c.status === "submitted" || c.run?.status === "running") return true;
  return !c.run || !trace;
}

/** The time the claim and trace queries poll until: a queued review's window, or the first-run watch while it applies. */
export function pollDeadline(reviewUntil: number, runWatchUntil: number, c: Pick<ClaimDetail, "status" | "run">, trace: Pick<Trace, "run"> | null | undefined): number {
  return Math.max(reviewUntil, waitsForWorker(c, trace) ? runWatchUntil : 0);
}

/** The trace page knows only the trace: check for a first run until `watchUntil`, and stop once a run exists. */
export function traceWatchPollMs(t: Pick<Trace, "run"> | null | undefined, watchUntil: number, now: number): number | false {
  if (t) return t.run.status === "running" ? 1_500 : false;
  return now < watchUntil ? WAIT_POLL_MS : false;
}

/** A queued review: the status and run it was queued against, and when the page stops polling for it. */
export type ReviewWait = { from: string; run: string | null; until: number };

const runKey = (c: Pick<ClaimDetail, "run">): string | null => (c.run ? `${c.run.run_id}:${c.run.status}` : null);

export function startReviewWait(c: Pick<ClaimDetail, "status" | "run">, now: number): ReviewWait {
  return { from: c.status, run: runKey(c), until: now + REVIEW_WAIT_MS };
}

/**
 * The flash once the worker has dealt with a queued review, or null while it has not: the status moved, or the
 * paused run moved on and is no longer running (a review can leave the status where it was).
 */
export function reviewSettled(w: ReviewWait, c: Pick<ClaimDetail, "status" | "run">): string | null {
  if (c.status !== w.from) return `The worker applied the review. The status went from ${w.from} to ${c.status}.`;
  if (runKey(c) !== w.run && c.run?.status !== "running") return `The worker finished with the review. The status is still ${c.status}; the trace shows what happened.`;
  return null;
}

export const REVIEW_STILL_WAITING = "The worker has not applied the review yet. It may be busy or stopped. Check again in a moment.";
export const RUN_STILL_WAITING = "The worker has not decided this claim yet. It may be busy or stopped. Check again in a moment.";

/** review.py answers 409 "a review is already pending for this claim" while an earlier review waits for the worker. */
export function isReviewQueued(p: Pick<ApiProblem, "status" | "detail">): boolean {
  return p.status === 409 && /already pending/i.test(p.detail ?? "");
}

/** A plain sentence for the reviewer-action conflicts review.py raises; undefined keeps the generic one. */
export function actionProblemSentence(p: Pick<ApiProblem, "status" | "detail">): string | undefined {
  if (isReviewQueued(p)) return "A review is already queued for this claim. The worker applies it in a moment; this page updates when it has.";
  if (p.status === 409 && /being adjudicated/i.test(p.detail ?? "")) return "The claim is being adjudicated right now. Try again when the run finishes.";
  return undefined;
}

export type CapUse = { label: string; used: string; max: string; ratio: number | null };

export function capUsage(t: Pick<Trace, "caps" | "used">): CapUse[] {
  const u = t.used;
  const ratio = (a: number, b: number) => (Number.isFinite(a) && Number.isFinite(b) && b > 0 ? Math.min(a / b, 1) : null);
  return [
    { label: "Steps", used: u ? String(u.steps) : "—", max: String(t.caps.max_steps), ratio: u ? ratio(u.steps, t.caps.max_steps) : null },
    { label: "Cost (USD)", used: u ? u.cost_usd : "—", max: t.caps.max_usd, ratio: u ? ratio(Number(u.cost_usd), Number(t.caps.max_usd)) : null },
    { label: "Time (s)", used: u ? String(u.seconds) : "—", max: String(t.caps.max_seconds), ratio: u ? ratio(u.seconds, t.caps.max_seconds) : null },
  ];
}

/** The last step's capped or error text, for the capped and failed run banners (trace.md). */
export function stopReason(t: Pick<Trace, "steps">): string | null {
  const last = t.steps.at(-1)?.result;
  if (!last) return null;
  if (last.capped !== undefined) return String(last.capped);
  if (typeof last.error === "string") return last.error;
  return null;
}

export type ActionKind = "approve" | "deny" | "request_info" | "mark_fraud" | "mark_delivered";

export const ACTION_LABEL: Record<ActionKind, string> = {
  approve: "Approve",
  deny: "Deny",
  request_info: "Request info",
  mark_fraud: "Mark fraud",
  mark_delivered: "Mark delivered",
};

/**
 * How much of each note the backend keeps: a request-info question reaches the claimant cut at 500 characters
 * (portal.py _safe_question), and a delivery note is stored cut at 300 (review.py _mark_delivered). The other notes
 * have no backend cut; 1,000 keeps the audit trail readable.
 */
export const NOTE_LIMIT: Record<ActionKind, number> = {
  approve: 1_000,
  deny: 1_000,
  request_info: 500,
  mark_fraud: 1_000,
  mark_delivered: 300,
};

/** The ClaimAction body for a reviewer's choice, validated against the contract before it is sent. */
export function buildAction(kind: ActionKind, message: string): { ok: true; body: ClaimAction } | { ok: false; error: string } {
  const note = message.trim();
  if (kind === "request_info" && !note) return { ok: false, error: "Write the question the claimant should answer." };
  if (note.length > NOTE_LIMIT[kind]) {
    return { ok: false, error: kind === "request_info" ? `Keep the question to ${NOTE_LIMIT[kind]} characters: the claimant sees no more than that.` : `Keep the note to ${NOTE_LIMIT[kind]} characters.` };
  }
  const raw =
    kind === "approve" || kind === "deny"
      ? { action: "resume_override", outcome: kind, message: note || null }
      : { action: kind, message: note || null };
  const parsed = ClaimAction.safeParse(raw);
  return parsed.success ? { ok: true, body: parsed.data } : { ok: false, error: "This action is not valid for the contract." };
}

export function isActionAvailable(kind: ActionKind, a: ActionAvailability): boolean {
  if (kind === "approve") return a.approve;
  if (kind === "deny") return a.deny;
  if (kind === "request_info") return a.requestInfo;
  if (kind === "mark_fraud") return a.markFraud;
  return a.markDelivered;
}

/** What to tell the reviewer after the API accepted an action (dev console wording, review.py semantics). */
export function actionResultMessage(kind: ActionKind, before: string, after: string, appliedByWorker: boolean): string {
  const label = ACTION_LABEL[kind];
  if (after !== before) return `${label}: done. The status went from ${before} to ${after}.`;
  return appliedByWorker
    ? `${label}: recorded. The worker resumes the paused run and applies it; this page refreshes until the status changes.`
    : `${label}: recorded. The status is still ${after}.`;
}

const PHOTO_ORDER = ["label", "destruction", "receipt", "other"];

/** Label first, then destruction, receipt, and anything else: the order a reviewer checks them. */
export function orderPhotos<T extends { kind: string; uploaded_at: string }>(photos: readonly T[]): T[] {
  const rank = (k: string) => (PHOTO_ORDER.includes(k) ? PHOTO_ORDER.indexOf(k) : PHOTO_ORDER.length);
  return [...photos].sort((a, b) => rank(a.kind) - rank(b.kind) || a.uploaded_at.localeCompare(b.uploaded_at));
}

export type ReasonPart = { text: string } | { claimId: string };

const CLAIM_ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

/** Split a decision reason so claim ids render as short links instead of 36-character strings. */
export function reasonParts(reason: string): ReasonPart[] {
  const parts: ReasonPart[] = [];
  let last = 0;
  for (const m of reason.matchAll(CLAIM_ID)) {
    if (m.index > last) parts.push({ text: reason.slice(last, m.index) });
    parts.push({ claimId: m[0].toLowerCase() });
    last = m.index + m[0].length;
  }
  if (last < reason.length) parts.push({ text: reason.slice(last) });
  return parts;
}

/** Duplicate-check hits that the Neo4j clusters do not already show (same kind and same other claim). */
export function newHits(hits: readonly DuplicateHit[], clusters: ReadonlyArray<{ kind: string; member_claim_ids: readonly string[] }>): DuplicateHit[] {
  return hits.filter((h) => !clusters.some((c) => c.kind === h.kind && c.member_claim_ids.includes(h.otherClaimId)));
}

/** A step that ran no model: pure code, with no tokens and no cost worth a column. */
export function isPureCode(step: Pick<TraceStep, "tokens_in" | "tokens_out" | "cost_usd" | "nominal_cost_usd">): boolean {
  return step.tokens_in === 0 && step.tokens_out === 0 && Number(step.cost_usd) === 0 && Number(step.nominal_cost_usd ?? 0) === 0;
}
