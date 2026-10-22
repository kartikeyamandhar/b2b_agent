// Review findings on the console: remedy options that follow the remedy, the close-program warning, the worker
// waits on claim detail, the request-info limit, per-period MPR boxes, partial export totals, and the docs.
import { readFileSync, readdirSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ReportsPage from "@/app/app/programs/[id]/reports/page";
import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { MprGenerate } from "@/components/MprGenerate";
import { ExportSummary } from "@/components/PayoutExports";
import { LifecycleControl, ProgramSettings } from "@/components/ProgramSettings";
import { ActionNote, ReviewerActions } from "@/components/ReviewerActions";
import { ClaimDetail, ExportBatchSummary, Program, Trace } from "@/lib/api-types";
import {
  NOTE_LIMIT,
  REVIEW_WAIT_MS,
  actionProblemSentence,
  buildAction,
  claimPollMs,
  isReviewQueued,
  pollDeadline,
  reviewSettled,
  startReviewWait,
  traceWatchPollMs,
  waitsForWorker,
} from "@/lib/claim-detail";
import { closeImpact, draftFromProgram, lifecycleBlock, optionsRequireDestructionPhoto, remedyOptionsFor, rulesPatch } from "@/lib/program-rules";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const programs = read("programs.json") as { items: unknown[] };
const open = Program.parse(programs.items[0]);
const draft = Program.parse(programs.items[1]);
const approved = ClaimDetail.parse(read("claim-detail.json"));
const trace = Trace.parse(read("trace-agent.json"));
const flagged = ClaimDetail.parse({ ...approved, status: "flagged", payout: null, run: trace.run });
const submitted = ClaimDetail.parse({ ...approved, status: "submitted", decision: null, decided_at: null, payout: null, run: null });
const html = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("1. remedy options follow the remedy chosen in the rules editor", () => {
  it("rebuilds a refund's options as a repair and keeps the destruction photo it required", () => {
    const r = rulesPatch({ ...draftFromProgram(open), remedyType: "repair" }, open);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.patch.remedy_rules?.remedy_type).toBe("repair");
    // portal.py submit takes the claim's remedy from options[0]: it must say repair, not the extracted refund.
    expect(r.patch.remedy_rules?.options).toEqual([{ type: "repair", steps: [], proof_required: ["destruction_photo"], payout: { method: null, amount_rule: null } }]);
  });

  it("reuses the extracted option of the chosen type and keeps every proof the options asked for", () => {
    const multi = Program.parse({
      ...(programs.items[0] as object),
      remedy_rules: {
        ...open.remedy_rules,
        remedy_type: "multiple",
        options: [
          { type: "refund", steps: ["Cut the cord"], proof_required: ["destruction_photo"], payout: { method: "check", amount_rule: "price" } },
          { type: "replacement", steps: ["Mail it back"], proof_required: ["receipt"], payout: { method: null, amount_rule: null } },
        ],
      },
    });
    const r = remedyOptionsFor("replacement", multi);
    expect(r).toEqual({ ok: true, changed: true, options: [{ type: "replacement", steps: ["Mail it back"], proof_required: ["destruction_photo", "receipt"], payout: { method: null, amount_rule: null } }] });
    expect(remedyOptionsFor("multiple", multi)).toMatchObject({ ok: true, changed: false });
  });

  it("refuses More than one remedy when the notice lists one, and an unset remedy while options name one", () => {
    const multiple = rulesPatch({ ...draftFromProgram(open), remedyType: "multiple" }, open);
    expect(multiple.ok).toBe(false);
    if (!multiple.ok) expect(multiple.errors.remedyType).toContain("Choose a single remedy");
    const unset = rulesPatch({ ...draftFromProgram(open), remedyType: "" }, open);
    expect(unset.ok).toBe(false);
    if (!unset.ok) expect(unset.errors.remedyType).toContain("still name one");
  });

  it("leaves a program without extracted options alone, and an unchanged remedy keeps its options", () => {
    const r = rulesPatch({ ...draftFromProgram(draft), remedyType: "repair", threshold: "100" }, draft);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.patch.remedy_rules).toEqual({ ...draft.remedy_rules, remedy_type: "repair" });
    const same = rulesPatch(draftFromProgram(open), open);
    if (same.ok) expect(same.patch.remedy_rules?.options).toEqual(open.remedy_rules.options);
  });

  it("says on the destruction field that an option keeps the destruction photo required", () => {
    expect(optionsRequireDestructionPhoto(open.remedy_rules.options ?? [])).toBe(true);
    expect(optionsRequireDestructionPhoto([{ proof_required: ["receipt"] }])).toBe(false);
    const out = renderToStaticMarkup(<ProgramSettings program={open} canAdmin />);
    expect(out).toContain("A destruction photo stays required while a remedy option below lists it");
    expect(out).toContain("Changing the remedy rebuilds the options for it");
    expect(renderToStaticMarkup(<ProgramSettings program={draft} canAdmin />)).not.toContain("stays required while a remedy option");
  });
});

describe("2. closing a product warns with the claims it cuts off", () => {
  it("counts each open status and the approved claims from claim_counts", () => {
    expect(closeImpact({ submitted: 1, needs_info: 2, flagged: 3, under_review: 4, capped: 5, approved: 1, draft: 2, denied: 9, paid: 9, closed: 9 })).toEqual([
      "1 claim submitted and not decided yet",
      "2 claims waiting for the claimant to answer a question",
      "3 claims flagged for a reviewer",
      "4 claims held for a reviewer before any money moves",
      "5 claims stopped at a checker limit and waiting for a reviewer",
      "1 approved claim whose claimant may still need to choose how a refund is paid",
      "2 claims started and not submitted yet",
    ]);
    expect(closeImpact({ denied: 4, paid: 2 })).toEqual([]);
  });

  it("shows the numbers before the close is confirmed and no longer says payouts stay as they are", () => {
    const out = renderToStaticMarkup(<LifecycleControl program={open} onChanged={() => {}} />);
    expect(out).toContain("data-close-impact");
    expect(out).toContain("107 claims waiting for the claimant to answer a question");
    expect(out).toContain("117 claims flagged for a reviewer");
    expect(out).toContain("72 approved claims whose claimants may still need to choose how a refund is paid");
    expect(out).toContain("choose how a refund is paid");
    expect(out).not.toContain("payouts already made stay as they are");
  });
});

describe("3. closing waits for unsaved rule edits", () => {
  it("blocks only a close, and only while the draft is dirty", () => {
    expect(lifecycleBlock("closed", true)).toBe("Save or discard your rule changes first. A closed product cannot be edited.");
    expect(lifecycleBlock("closed", false)).toBeNull();
    expect(lifecycleBlock("open", true)).toBeNull();
  });

  it("disables the close button and says why", () => {
    const out = renderToStaticMarkup(<LifecycleControl program={open} onChanged={() => {}} blocked={lifecycleBlock("closed", true)} />);
    expect(out).toContain("Save or discard your rule changes first.");
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*data-lifecycle-start/);
    expect(renderToStaticMarkup(<LifecycleControl program={open} onChanged={() => {}} />)).not.toMatch(/<button[^>]*disabled=""[^>]*data-lifecycle-start/);
  });
});

describe("4. a queued review holds the actions until the worker applies it", () => {
  it("disables every action with a waiting note while pending", () => {
    const out = html(<ReviewerActions claim={flagged} onDone={() => {}} pending />);
    expect(out).toContain("data-review-pending");
    const buttons = out.match(/<button[^>]*data-action="[a-z_]+"[^>]*>/g) ?? [];
    expect(buttons.length).toBeGreaterThan(0);
    for (const b of buttons) expect(b).toContain('disabled=""');
    const idle = html(<ReviewerActions claim={flagged} onDone={() => {}} />);
    expect(idle).not.toContain("data-review-pending");
    expect(idle).not.toMatch(/<button[^>]*disabled=""[^>]*data-action=/);
  });

  it("reads the 409 for an already queued review as a sentence, not a stale page", () => {
    const queued = { status: 409, detail: "a review is already pending for this claim" };
    expect(isReviewQueued(queued)).toBe(true);
    expect(actionProblemSentence(queued)).toContain("A review is already queued for this claim");
    expect(actionProblemSentence({ status: 409, detail: "the claim is being adjudicated; try again when the run finishes" })).toContain("being adjudicated right now");
    expect(actionProblemSentence({ status: 409, detail: "a claim in status paid cannot be reviewed" })).toBeUndefined();
    expect(isReviewQueued({ status: 422, detail: "already pending" })).toBe(false);
  });

  it("settles when the status moves or the paused run moves on, and not before", () => {
    const w = startReviewWait(flagged, 1_000);
    expect(w).toEqual({ from: "flagged", run: `${trace.run.run_id}:waiting_human`, until: 1_000 + REVIEW_WAIT_MS });
    expect(reviewSettled(w, flagged)).toBeNull();
    expect(reviewSettled(w, { ...flagged, run: { ...trace.run, status: "running" } })).toBeNull();
    expect(reviewSettled(w, { ...flagged, status: "approved" })).toBe("The worker applied the review. The status went from flagged to approved.");
    expect(reviewSettled(w, { ...flagged, run: { ...trace.run, status: "done" } })).toContain("The status is still flagged");
  });
});

describe("5. a submitted claim with no run is watched for its first run", () => {
  const done = { ...trace.run, status: "done" };

  it("waits while submitted or without a run or trace, and stops once a finished run exists", () => {
    expect(waitsForWorker(submitted, null)).toBe(true);
    expect(waitsForWorker({ ...submitted, run: done }, { run: done })).toBe(true); // reopened: decided again
    expect(waitsForWorker({ ...approved, run: done }, { run: done })).toBe(false);
    expect(waitsForWorker({ ...approved, run: done }, null)).toBe(true); // the trace has not loaded yet
    expect(waitsForWorker({ ...submitted, status: "draft" }, null)).toBe(false);
  });

  it("polls the claim every 2 s for the watch window only", () => {
    const until = pollDeadline(0, 120_000, submitted, null);
    expect(until).toBe(120_000);
    expect(claimPollMs(submitted, until, 1_000)).toBe(2_000);
    expect(claimPollMs(submitted, until, 121_000)).toBe(false);
    expect(pollDeadline(0, 120_000, { ...approved, run: done }, { run: done })).toBe(0);
    expect(pollDeadline(70_000, 120_000, { ...approved, run: done }, { run: done })).toBe(70_000);
  });

  it("gives the trace page a capped watch for a run that does not exist yet", () => {
    expect(traceWatchPollMs(null, 120_000, 1_000)).toBe(2_000);
    expect(traceWatchPollMs(undefined, 120_000, 1_000)).toBe(2_000);
    expect(traceWatchPollMs(null, 120_000, 120_001)).toBe(false);
    expect(traceWatchPollMs({ run: done }, 120_000, 1_000)).toBe(false);
    expect(traceWatchPollMs({ run: { ...done, status: "running" } }, 120_000, 1_000)).toBe(1_500);
  });

  it("tells the reviewer the page is checking", () => {
    const out = html(<ClaimDetailLive initialClaim={submitted} initialTrace={null} loadedAt={Date.now()} />);
    expect(out).toContain("Not adjudicated yet");
    expect(out).toContain('data-run-watch="polling"');
    expect(html(<ClaimDetailLive initialClaim={approved} initialTrace={{ ...trace, run: done }} loadedAt={Date.now()} />)).not.toContain("data-run-watch");
  });
});

describe("6. a request-info question stops at what the claimant can read", () => {
  it("uses the backend's limits", () => {
    expect(NOTE_LIMIT.request_info).toBe(500); // portal.py _safe_question: [:500]
    expect(NOTE_LIMIT.mark_delivered).toBe(300); // review.py _mark_delivered: note[:300]
    expect(buildAction("request_info", "q".repeat(500)).ok).toBe(true);
    const long = buildAction("request_info", "q".repeat(501));
    expect(long).toEqual({ ok: false, error: "Keep the question to 500 characters: the claimant sees no more than that." });
  });

  it("caps the box and shows a live count", () => {
    const out = renderToStaticMarkup(<ActionNote kind="request_info" message="Is the cord cut?" onChange={() => {}} />);
    expect(out).toContain('maxLength="500"');
    expect(out).toContain("16 of 500 characters");
    expect(renderToStaticMarkup(<ActionNote kind="approve" message="" onChange={() => {}} />)).toContain('maxLength="1000"');
  });
});

describe("7. MPR boxes belong to one period", () => {
  const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
  const generators = (node: ReactNode): ReactElement[] => {
    if (Array.isArray(node)) return node.flatMap(generators);
    if (!isValidElement(node)) return [];
    const self = node.type === MprGenerate ? [node] : [];
    return [...self, ...generators((node.props as { children?: ReactNode }).children)];
  };

  it("keys the generator by period, so a second period starts with no values from the first", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    vi.stubGlobal("fetch", async (url: string) =>
      new URL(url).pathname.endsWith("/mpr") ? Response.json({ title: "Not Found", status: 404, detail: "no report for that period; generate one first" }, { status: 404 }) : Response.json(programs.items[0]),
    );
    const page = (period: string) => ReportsPage({ params: Promise.resolve({ id: P }), searchParams: Promise.resolve({ period }) });
    const [october] = generators(await page("2026-10"));
    const [september] = generators(await page("2026-09"));
    // React keeps a component's state only while its type and key match; a new key mounts it with empty boxes.
    expect(october?.key).toBe("2026-10");
    expect(september?.key).toBe("2026-09");
  });
});

describe("8. export totals say when they cover only the batches shown", () => {
  const batch = (i: number) =>
    ExportBatchSummary.parse({ id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, created_at: "2026-10-20T00:00:00Z", created_by: "seed", row_count: 2, total_cents: 1_000, sha256: "a".repeat(64) });

  it("labels totals all-time under the list limit and partial at it", () => {
    const few = renderToStaticMarkup(<ExportSummary items={[batch(1), batch(2)]} />);
    expect(few).toContain("Payouts exported");
    expect(few).not.toContain("newest batches");
    const many = renderToStaticMarkup(<ExportSummary items={Array.from({ length: 100 }, (_, i) => batch(i))} />);
    expect(many).toContain("Batches shown");
    expect(many).toContain("Total in these batches");
    expect(many).toContain("These totals cover the 100 newest batches. Older batches are not counted.");
    expect(many).not.toContain(">Total exported<");
  });
});

describe("9. the web README and root layout describe what shipped", () => {
  const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
  const layoutSection = readme.slice(readme.indexOf("## Layout"), readme.indexOf("## Design system"));

  it("has a current status line and lists every component and library module", () => {
    expect(readme).not.toContain("Phase 1B");
    for (const route of ["/app/programs/[id]/settings", "/app/programs/[id]/reports", "/app/programs/[id]/fraud", "/app/programs/[id]/campaign", "/app/payouts", "/app/billing"]) {
      expect(readme.slice(0, readme.indexOf("## Run"))).toContain(route);
    }
    const names = [
      ...readdirSync(new URL("../src/components", import.meta.url)).filter((f) => f.endsWith(".tsx")).map((f) => f.replace(/\.tsx$/, "")),
      ...readdirSync(new URL("../src/components/portal", import.meta.url)).filter((f) => f.endsWith(".tsx")).map((f) => f.replace(/\.tsx$/, "")),
    ];
    for (const n of names) expect(layoutSection, n).toContain(n);
    for (const f of readdirSync(new URL("../src/lib", import.meta.url)).filter((x) => x.endsWith(".ts"))) expect(layoutSection, f).toContain(`src/lib/${f}`);
  });

  it("says where Clerk is provided instead of promising it later", () => {
    const root = readFileSync(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
    expect(root).not.toContain("once component 4 lands");
    expect(root).toContain("ClerkShell");
  });
});
