import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import * as api from "@/lib/api-types";
import {
  actionAvailability,
  isPureCode,
  newHits,
  orderPhotos,
  reasonParts,
  actionResultMessage,
  buildAction,
  capUsage,
  claimPollMs,
  clusterLabel,
  describeResult,
  duplicateHits,
  photoSrc,
  readingsByPhotoKind,
  splitCitations,
  stopReason,
  tracePollMs,
} from "@/lib/claim-detail";

const trace = api.Trace.parse(JSON.parse(readFileSync(new URL("./fixtures/trace-agent.json", import.meta.url), "utf8")));
const run = (graph: string, status: string) => ({ run_id: "r", graph, status, steps: 1, cost_usd: "0", started_at: "", finished_at: null });
const C = "66dcc492-086f-5b59-b441-cbe001eb2e0b";

describe("actionAvailability (review.py)", () => {
  it.each(["flagged", "under_review", "capped", "needs_info", "approved", "denied"])("offers info and fraud on %s", (status) => {
    const a = actionAvailability({ status, remedy: "refund", run: null });
    expect(a).toMatchObject({ requestInfo: true, markFraud: true, markDelivered: false, blocked: null });
  });

  it("does not offer the decision a claim already holds", () => {
    expect(actionAvailability({ status: "approved", remedy: "refund", run: null })).toMatchObject({ approve: false, deny: true });
    expect(actionAvailability({ status: "denied", remedy: "refund", run: null })).toMatchObject({ approve: true, deny: false });
    for (const s of ["flagged", "under_review", "capped", "needs_info"]) {
      expect(actionAvailability({ status: s, remedy: "refund", run: null })).toMatchObject({ approve: true, deny: true });
    }
  });

  it.each(["draft", "submitted", "paid", "closed", "archived"])("offers nothing on %s and says why", (status) => {
    const a = actionAvailability({ status, remedy: "refund", run: null });
    expect(a.approve || a.deny || a.requestInfo || a.markFraud || a.markDelivered).toBe(false);
    expect(a.blocked).toBeTruthy();
  });

  it("explains an unrecognized status instead of guessing", () => {
    expect(actionAvailability({ status: "archived", remedy: null, run: null }).blocked).toContain('"archived" is not recognized');
  });

  it("offers mark delivered only for an approved repair or replacement", () => {
    expect(actionAvailability({ status: "approved", remedy: "repair", run: null }).markDelivered).toBe(true);
    expect(actionAvailability({ status: "approved", remedy: "replacement", run: null }).markDelivered).toBe(true);
    expect(actionAvailability({ status: "approved", remedy: "refund", run: null }).markDelivered).toBe(false);
    expect(actionAvailability({ status: "denied", remedy: "repair", run: null }).markDelivered).toBe(false);
  });

  it("blocks every action while an agent run is running (409 otherwise)", () => {
    const a = actionAvailability({ status: "flagged", remedy: "refund", run: run("adjudicate", "running") as never });
    expect(a.blocked).toMatch(/being adjudicated/);
    expect(a.approve || a.deny).toBe(false);
  });

  it("knows when the worker applies the action to a paused run", () => {
    for (const s of ["waiting_human", "capped", "failed"]) {
      expect(actionAvailability({ status: "flagged", remedy: null, run: run("adjudicate", s) as never }).appliedByWorker).toBe(true);
    }
    expect(actionAvailability({ status: "flagged", remedy: null, run: run("rules_only", "done") as never }).appliedByWorker).toBe(false);
    expect(actionAvailability({ status: "needs_info", remedy: null, run: run("adjudicate", "waiting_human") as never }).appliedByWorker).toBe(false);
  });
});

describe("buildAction", () => {
  it("builds the contract bodies", () => {
    expect(buildAction("approve", " checked by hand ")).toEqual({ ok: true, body: { action: "resume_override", outcome: "approve", message: "checked by hand" } });
    expect(buildAction("deny", "")).toEqual({ ok: true, body: { action: "resume_override", outcome: "deny", message: null } });
    expect(buildAction("mark_fraud", "")).toEqual({ ok: true, body: { action: "mark_fraud", message: null } });
    expect(buildAction("mark_delivered", "kit shipped")).toEqual({ ok: true, body: { action: "mark_delivered", message: "kit shipped" } });
  });

  it("requires a question for request info", () => {
    expect(buildAction("request_info", "   ")).toEqual({ ok: false, error: "Write the question the claimant should answer." });
    expect(buildAction("request_info", "Photo of the label?")).toMatchObject({ ok: true, body: { action: "request_info", message: "Photo of the label?" } });
  });

  it("produces bodies the strict request schema accepts", () => {
    for (const k of ["approve", "deny", "request_info", "mark_fraud", "mark_delivered"] as const) {
      const r = buildAction(k, "x");
      expect(r.ok && api.ClaimAction.safeParse(r.body).success).toBe(true);
    }
  });

  it("words the result for a direct and a worker-applied action", () => {
    expect(actionResultMessage("approve", "flagged", "approved", false)).toBe("Approve: done. The status went from flagged to approved.");
    expect(actionResultMessage("deny", "flagged", "flagged", true)).toMatch(/worker resumes the paused run/);
    expect(actionResultMessage("mark_fraud", "denied", "denied", false)).toBe("Mark fraud: recorded. The status is still denied.");
  });
});

describe("trace readings", () => {
  it("maps each photo kind to its latest reading", () => {
    const r = readingsByPhotoKind(trace);
    expect(r.label).toMatchObject({ tool: "read_label_photo", idx: 2, resultId: "T1", problem: false });
    expect(r.label!.text).toBe("Label read: model TL1302Z, serial not visible, date code 2512 (confidence 94%).");
    expect(r.destruction).toMatchObject({ tool: "check_destruction_photo", resultId: "T3", problem: true });
    expect(r.destruction!.text).toContain("not visible. The laminator is intact");
    expect(r.receipt).toBeUndefined();
    expect(readingsByPhotoKind(null)).toEqual({});
  });

  it.each([
    ["read_label_photo", { legible: false }, "could not be read", true],
    ["read_label_photo", { error: "missing recording" }, "The check failed: missing recording", true],
    ["check_destruction_presence", { verdict: "present", reason: "destruction photo present (content not checked in M1)" }, "A destruction photo is present. The rules-only checker does not check what it shows.", false],
    ["check_destruction_presence", { verdict: "missing", reason: "no destruction photo" }, "No usable destruction photo: no destruction photo", true],
    ["check_destruction_photo", { verdict: "not_required" }, "does not require", false],
    ["check_destruction_photo", { verdict: "checked", complies: true, observed: "Cord cut.", confidence: 0.9 }, "is visible. Cord cut.", false],
    ["check_purchase", { receipt: "missing", retailer: "listed", window: "inside" }, "Receipt missing; retailer is on the sold-at list; bought inside the recall window.", true],
    ["check_model", { capped: "steps" }, "stopped at a cap (steps)", true],
    ["retrieve_policy", { hits: [] }, "0 policy passages retrieved.", false],
    ["check_model", { verdict: "match", cited_rule: "TL1302Z" }, "Model is part of the recall (TL1302Z).", false],
    ["check_model", { verdict: "mismatch" }, "Model is not part of the recall.", true],
    ["check_serial", { verdict: "skipped", cited_rule: null }, "Not checked: the recall lists no serial number rule.", false],
    ["check_date_code", { verdict: "out_of_range" }, "Date code is outside the recalled range.", true],
    ["check_duplicates", { hits: [{}, {}] }, "2 possible duplicates found.", true],
    ["check_duplicates", { hits: [] }, "No duplicates found.", false],
    ["decide", { accepted: false, error: "outcome: invalid" }, "The check failed: outcome: invalid", true],
    ["route", { interrupt: "human_review" }, "See the trace", false],
  ] as const)("describes %s %j", (tool, result, text, problem) => {
    const d = describeResult(tool, result as Record<string, unknown>);
    expect(d.text).toContain(text);
    expect(d.problem).toBe(problem);
  });

  it("reads duplicate hits and ignores malformed ones", () => {
    expect(duplicateHits(trace)).toEqual([{ kind: "same_photo", otherClaimId: "7a1e0c2d-3b4f-5a6b-8c9d-0e1f2a3b4c5d", detail: "label photo pHash within 4 bits" }]);
    const odd = { steps: [{ ...trace.steps[5]!, result: { hits: ["x", null, { kind: "same_serial" }] } }] };
    expect(duplicateHits(odd)).toEqual([]);
  });

  it("splits rule and result citations", () => {
    expect(splitCitations(["R:model", "T1", "T12", "TL1302Z"])).toEqual({ results: ["T1", "T12"], rules: ["R:model", "TL1302Z"] });
  });

  it("reports caps use and the stop reason", () => {
    expect(capUsage(trace)).toEqual([
      { label: "Steps", used: "4", max: "8", ratio: 0.5 },
      { label: "Cost (USD)", used: "0.003120", max: "0.02", ratio: 0.156 },
      { label: "Time (s)", used: "6.4", max: "60", ratio: 6.4 / 60 },
    ]);
    expect(capUsage({ caps: trace.caps, used: null })[0]).toMatchObject({ used: "—", ratio: null });
    expect(stopReason({ steps: [{ ...trace.steps[0]!, result: { capped: "usd cap: 0.0201 > 0.02" } }] })).toBe("usd cap: 0.0201 > 0.02");
    expect(stopReason(trace)).toBeNull();
  });
});

describe("photos, clusters and polling", () => {
  it("renders only API photo paths for this claim, through the proxy", () => {
    expect(photoSrc(`/v1/claims/${C}/photos/p1`, C)).toBe(`/api/v1/claims/${C}/photos/p1`);
    expect(photoSrc("https://storage.example.com/x.jpg", C)).toBeNull();
    expect(photoSrc(`/v1/claims/other/photos/p1`, C)).toBeNull();
    expect(photoSrc(`/v1/claims/${C}/photos/../../internal`, C)).toBeNull();
  });

  it("labels every contract cluster kind", () => {
    for (const k of api.ClusterKindValues) expect(clusterLabel(k)).not.toContain("(");
    expect(clusterLabel("same_device")).toBe("Linked to other claims (same_device)");
  });

  it("polls the claim only while a run is running or a review is pending", () => {
    expect(claimPollMs({ run: run("adjudicate", "running") as never }, 0, 1000)).toBe(2000);
    expect(claimPollMs({ run: run("adjudicate", "waiting_human") as never }, 0, 1000)).toBe(false);
    expect(claimPollMs({ run: null }, 5000, 1000)).toBe(2000);
    expect(claimPollMs({ run: null }, 5000, 6000)).toBe(false);
    expect(tracePollMs({ run: run("adjudicate", "running") as never })).toBe(1500);
    expect(tracePollMs({ run: run("adjudicate", "done") as never })).toBe(false);
    expect(tracePollMs(null)).toBe(false);
  });
});

describe("reviewer readability helpers", () => {
  it("orders photos label, destruction, receipt, then anything else", () => {
    const p = (kind: string, t: string) => ({ kind, uploaded_at: t });
    expect(orderPhotos([p("receipt", "1"), p("other", "0"), p("destruction", "2"), p("label", "3")]).map((x) => x.kind)).toEqual(["label", "destruction", "receipt", "other"]);
  });

  it("turns claim ids in a reason into link parts", () => {
    expect(reasonParts("duplicate same_payout with claim 768DC2DB-e31c-5ee6-8062-665cf67dfdd1 (payout destination)")).toEqual([
      { text: "duplicate same_payout with claim " },
      { claimId: "768dc2db-e31c-5ee6-8062-665cf67dfdd1" },
      { text: " (payout destination)" },
    ]);
    expect(reasonParts("every check passed")).toEqual([{ text: "every check passed" }]);
  });

  it("drops duplicate-check hits a cluster already shows", () => {
    const hits = [{ kind: "same_photo", otherClaimId: "a", detail: "" }, { kind: "same_address", otherClaimId: "a", detail: "" }];
    expect(newHits(hits, [{ kind: "same_photo", member_claim_ids: ["me", "a"] }])).toEqual([hits[1]]);
  });

  it("knows a pure-code step from a model step", () => {
    expect(isPureCode(trace.steps[3]!)).toBe(true);
    expect(isPureCode(trace.steps[1]!)).toBe(false);
  });
});
