// Console labels for the backend's status vocabularies (docs/contracts/trace.md, CHANGES.md items 5 and 6).
// A value the backend adds later renders as "In progress" instead of failing (CHANGES.md item 5).

export type Tone = "neutral" | "progress" | "done" | "attention" | "negative";
export type StatusKind = "claim" | "run" | "payout" | "outcome" | "program";
export type StatusLabel = { label: string; tone: Tone; known: boolean };

const CLAIM: Record<string, Omit<StatusLabel, "known">> = {
  // Plan v3: a draft is a claim someone started from the product's claim link and has not submitted.
  draft: { label: "Started, not submitted", tone: "neutral" },
  submitted: { label: "Submitted", tone: "progress" },
  needs_info: { label: "Needs info", tone: "attention" },
  under_review: { label: "Under review", tone: "attention" },
  approved: { label: "Approved", tone: "done" },
  denied: { label: "Denied", tone: "negative" },
  flagged: { label: "Flagged", tone: "attention" },
  capped: { label: "Capped", tone: "attention" },
  paid: { label: "Paid", tone: "done" },
  closed: { label: "Closed", tone: "done" },
};

const RUN: Record<string, Omit<StatusLabel, "known">> = {
  running: { label: "Running", tone: "progress" },
  done: { label: "Done", tone: "done" },
  capped: { label: "Capped", tone: "attention" },
  failed: { label: "Failed", tone: "negative" },
  waiting_human: { label: "Waiting for reviewer", tone: "attention" },
};

const PAYOUT: Record<string, Omit<StatusLabel, "known">> = {
  pending: { label: "Pending", tone: "progress" },
  pending_export: { label: "Waiting for check export", tone: "progress" },
  onboarding: { label: "Claimant onboarding", tone: "progress" },
  transferring: { label: "Transfer in flight", tone: "progress" },
  transferred: { label: "Transferred", tone: "done" },
  failed: { label: "Transfer failed", tone: "negative" },
  exported: { label: "Check exported", tone: "done" },
  held: { label: "On hold, a person is reviewing", tone: "attention" },
};

const OUTCOME: Record<string, Omit<StatusLabel, "known">> = {
  approve: { label: "Approve", tone: "done" },
  deny: { label: "Deny", tone: "negative" },
  needs_info: { label: "Needs info", tone: "attention" },
  flag: { label: "Flag", tone: "attention" },
};

const PROGRAM: Record<string, Omit<StatusLabel, "known">> = {
  draft: { label: "Draft", tone: "neutral" },
  open: { label: "Open", tone: "progress" },
  closed: { label: "Closed", tone: "done" },
};

const TABLES: Record<StatusKind, Record<string, Omit<StatusLabel, "known">>> = {
  claim: CLAIM,
  run: RUN,
  payout: PAYOUT,
  outcome: OUTCOME,
  program: PROGRAM,
};

export function statusLabel(kind: StatusKind, value: string | null | undefined): StatusLabel {
  if (value === null || value === undefined || value === "") return { label: "None", tone: "neutral", known: true };
  const hit = Object.hasOwn(TABLES[kind], value) ? TABLES[kind][value] : undefined;
  return hit ? { ...hit, known: true } : { label: "In progress", tone: "progress", known: false };
}

export function knownValues(kind: StatusKind): string[] {
  return Object.keys(TABLES[kind]);
}

// Words for the backend's other identifiers: graph names, who decided, proof kinds, and audit actions. The API keeps
// its identifiers (several are frozen with the ablation), so the console words them here. One this file does not know
// reads as words ("payout_hold_cleared" as "Payout hold cleared"), never as snake_case.

/** "payout_hold_cleared" as "Payout hold cleared". */
export function humanize(value: string): string {
  const words = value.replaceAll("_", " ").replace(/\s+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : value;
}

/** agent_runs.graph (trace.md). */
export const GRAPH_LABEL: Record<string, string> = {
  adjudicate: "Adjudication agent",
  rules_only: "Rules-only checker",
  rules_plus_vision: "Rules plus vision (evaluation)",
  extract_rules: "Rule extraction",
};

export function graphLabel(graph: string): string {
  return Object.hasOwn(GRAPH_LABEL, graph) ? GRAPH_LABEL[graph]! : humanize(graph);
}

/** claims.decided_by: "agent", "rules_only", or the reviewer's user id (schemas.py), which is shown as it is. */
const DECIDER: Record<string, string> = {
  agent: "the adjudication agent",
  rules_only: "the rules-only checker",
  rules_plus_vision: "the rules plus vision checker (evaluation)",
};

/** Reads after "by": "by the rules-only checker", "by user_2abc". */
export function decidedByLabel(by: string | null | undefined): string {
  if (!by) return "an unknown decider";
  return Object.hasOwn(DECIDER, by) ? DECIDER[by]! : by;
}

/** remedy option proof_required (rules/schemas.py ProofKind). */
const PROOF_LABEL: Record<string, string> = {
  label_photo: "label photo",
  destruction_photo: "destruction photo",
  receipt: "receipt",
  product_return: "product return",
  registration: "registration",
};

function listJoin(items: readonly string[]): string {
  if (items.length < 3) return items.join(" and ");
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}

/** "Proof required: destruction photo, receipt, and registration." A list of only "none" says so. */
export function proofSentence(proofs: readonly string[]): string | null {
  const real = proofs.filter((p) => p !== "none");
  if (proofs.length === 0) return null;
  if (real.length === 0) return "No proof required.";
  const words = real.map((p) => (Object.hasOwn(PROOF_LABEL, p) ? PROOF_LABEL[p]! : p.replaceAll("_", " ")));
  return `Proof required: ${listJoin(words)}.`;
}

/** audit_log.action values a claim's trail carries (claims/effects.py, review.py, portal.py, payouts). */
const AUDIT_ACTION_LABEL: Record<string, string> = {
  claim_submitted: "Submitted",
  claim_answered: "Claimant answered",
  claim_adjudicated: "Adjudicated",
  claim_decided: "Decided",
  claim_reviewed: "Reviewed",
  claim_paid: "Paid",
  claim_held: "Held for a person",
  claim_held_for_review: "Held for a person",
  claim_held_shared_mailing: "Held: shared mailing address",
  claim_reopened_duplicate: "Reopened by a later duplicate",
  claim_link_reissued: "Claim link reissued",
  claim_links_issued: "Claim links issued",
  review_submitted: "Review submitted",
  review_voided: "Review voided",
  review_voided_by_duplicate: "Review voided by a duplicate",
};

export function auditActionLabel(action: string): string {
  return Object.hasOwn(AUDIT_ACTION_LABEL, action) ? AUDIT_ACTION_LABEL[action]! : humanize(action);
}
