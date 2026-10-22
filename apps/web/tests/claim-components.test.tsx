import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { AuditTrail, DecisionPanel, FraudPanel, PayoutPanel, PhotoEvidence } from "@/components/ClaimPanels";
import { ReviewerActions } from "@/components/ReviewerActions";
import { TraceView } from "@/components/TraceView";
import { ClaimDetail, Trace } from "@/lib/api-types";
import { readingsByPhotoKind } from "@/lib/claim-detail";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const approved = ClaimDetail.parse(read("claim-detail.json"));
const trace = Trace.parse(read("trace-agent.json"));
const flagged = ClaimDetail.parse({
  ...approved,
  status: "flagged",
  payout: null,
  decision: { outcome: "flag", reasons: ["destruction not visible", "same photo as another claim"], cited_rules: ["R:destruction", "T3", "T4"], routed_to_human: true, confidence: 0.7, decided_by: "agent" },
  run: trace.run,
  clusters: [{ id: "c1", kind: "same_photo", score: 0.97, member_claim_ids: [approved.id, "7a1e0c2d-3b4f-5a6b-8c9d-0e1f2a3b4c5d"] }],
  photos: [...approved.photos, { ...approved.photos[0]!, id: "9a1d0d5e-7c3b-4c1e-9a52-1f0f2b6a9a02", kind: "destruction", url: `/v1/claims/${approved.id}/photos/9a1d0d5e-7c3b-4c1e-9a52-1f0f2b6a9a02` }],
});
const html = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

describe("PhotoEvidence", () => {
  const out = renderToStaticMarkup(<PhotoEvidence claimId={flagged.id} photos={flagged.photos} readings={readingsByPhotoKind(trace)} />);

  it("loads photo bytes through the same-origin proxy with alt text", () => {
    expect(out).toContain(`src="/api/v1/claims/${flagged.id}/photos/8a1d0d5e-7c3b-4c1e-9a52-1f0f2b6a9a01"`);
    expect(out).toContain('alt="Label photo submitted with the claim"');
  });

  it("puts each reading beside its photo with a link to the step", () => {
    expect(out).toContain("Label read: model TL1302Z");
    expect(out).toContain("The instructed destruction is not visible.");
    expect(out).toContain(`href="/app/claims/${flagged.id}/trace#step-4"`);
    expect(out).toContain("step 4 · T3");
  });

  it("never renders a photo URL that is not an API path", () => {
    const bad = [{ ...flagged.photos[0]!, url: "https://bucket.example.com/a.jpg" }];
    const o = renderToStaticMarkup(<PhotoEvidence claimId={flagged.id} photos={bad} readings={{}} />);
    expect(o).not.toContain("bucket.example.com");
    expect(o).toContain("Photo unavailable");
    expect(o).toContain("No reading of this photo");
  });

  it("says when there are no photos", () => {
    expect(renderToStaticMarkup(<PhotoEvidence claimId="x" photos={[]} readings={{}} />)).toContain("No photos were uploaded");
  });
});

describe("DecisionPanel", () => {
  it("shows the outcome, reasons, and citations linked to trace results", () => {
    const out = renderToStaticMarkup(<DecisionPanel claimId={flagged.id} decision={flagged.decision} decidedAt={flagged.decided_at} />);
    expect(out).toContain('data-status="flag"');
    expect(out).toContain("destruction not visible");
    expect(out).toContain("R:destruction");
    expect(out).toContain(`href="/app/claims/${flagged.id}/trace#result-T3"`);
    const withId = renderToStaticMarkup(<DecisionPanel claimId="x" decision={{ ...flagged.decision!, reasons: ["duplicate same_payout with claim 768dc2db-e31c-5ee6-8062-665cf67dfdd1"] }} decidedAt={null} />);
    expect(withId).toContain('href="/app/claims/768dc2db-e31c-5ee6-8062-665cf67dfdd1"');
    expect(withId).toContain(">768dc2db<");
    expect(out).toContain("confidence 70%");
    expect(out).toContain("routed to a person");
  });

  it("says when there is no decision", () => {
    expect(renderToStaticMarkup(<DecisionPanel claimId="x" decision={null} decidedAt={null} />)).toContain("No decision yet");
  });

  it("says why a claim waits for a person and which later claim reopened it", () => {
    const held = { ...flagged.decision!, outcome: "approve" as const, hold_reason: "payout_held_needs_review", reopened_from: "approved", duplicate_found_later: ["768dc2db-e31c-5ee6-8062-665cf67dfdd1"] };
    const out = renderToStaticMarkup(<DecisionPanel claimId="x" decision={held} decidedAt={null} />);
    expect(out).toContain("data-hold-reason");
    expect(out).toContain("A held payout needs a person&#x27;s approval before money moves.");
    expect(out).toContain("Reopened from approved");
    expect(out).toContain('href="/app/claims/768dc2db-e31c-5ee6-8062-665cf67dfdd1"');
    const unknown = renderToStaticMarkup(<DecisionPanel claimId="x" decision={{ ...held, hold_reason: "some_new_reason", reopened_from: null }} decidedAt={null} />);
    expect(unknown).toContain("some new reason");
    expect(unknown).not.toContain("data-reopened");
    expect(renderToStaticMarkup(<DecisionPanel claimId="x" decision={flagged.decision} decidedAt={null} />)).not.toContain("data-hold-reason");
  });
});

describe("PayoutPanel", () => {
  it("shows the ledger state of a refund", () => {
    const out = renderToStaticMarkup(<PayoutPanel payout={approved.payout} remedy="refund" />);
    expect(out).toContain("Waiting for check export");
    expect(out).toContain("$29.99 USD");
    expect(out).toContain("Mailed check");
  });

  it("highlights a held payout and a reversal", () => {
    const out = renderToStaticMarkup(<PayoutPanel payout={{ ...approved.payout!, status: "held", stripe_reversed_cents: 500 }} remedy="refund" />);
    expect(out).toContain("On hold, a person is reviewing");
    expect(out).toContain("$5.00 reversed; an operator must review it");
    expect(out).toContain("border-l-warn");
  });

  it("explains repairs and replacements have no payout", () => {
    expect(renderToStaticMarkup(<PayoutPanel payout={null} remedy="repair" />)).toContain("Repair remedies are not paid out");
  });

  it("notes that transferred is not bank arrival", () => {
    expect(renderToStaticMarkup(<PayoutPanel payout={{ ...approved.payout!, status: "transferred" }} remedy="refund" />)).toContain("not that the bank received it");
  });
});

describe("FraudPanel", () => {
  it("lists cluster members other than this claim, once even when the duplicate check found the same link", () => {
    const out = renderToStaticMarkup(
      <FraudPanel claimId={flagged.id} clusters={flagged.clusters} hits={[{ kind: "same_photo", otherClaimId: "7a1e0c2d-3b4f-5a6b-8c9d-0e1f2a3b4c5d", detail: "pHash within 4 bits" }]} />,
    );
    expect(out).toContain("Same photo as another claim");
    expect(out).toContain("1 other claim in this group");
    expect(out.match(/href="\/app\/claims\/7a1e0c2d-3b4f-5a6b-8c9d-0e1f2a3b4c5d"/g)).toHaveLength(1);
    expect(out).not.toContain(`href="/app/claims/${flagged.id}"`);
    expect(out).not.toContain("Duplicate check:");
  });

  it("shows a duplicate-check hit the clusters do not cover", () => {
    const out = renderToStaticMarkup(
      <FraudPanel claimId={flagged.id} clusters={flagged.clusters} hits={[{ kind: "same_serial", otherClaimId: "9c3a2e4f-5d6b-7c8d-0e1f-2a3b4c5d6e7f", detail: "serial SN1 repeated" }]} />,
    );
    expect(out).toContain("Duplicate check: Same serial number as another claim");
    expect(out).toContain("serial SN1 repeated");
  });

  it("says when there are no indicators", () => {
    expect(renderToStaticMarkup(<FraudPanel claimId="x" clusters={[]} hits={[]} />)).toContain("No duplicate or fraud indicators");
  });
});

describe("AuditTrail", () => {
  it("shows status changes and escapes notes", () => {
    const out = renderToStaticMarkup(
      <AuditTrail entries={[...approved.audit, { action: "review_submitted", actor: "dev-user", at: "2026-10-20T09:00:00Z", before: { status: "flagged" }, after: { review: { message: "<b>hi</b>" } } }]} />,
    );
    expect(out).toContain("Started, not submitted → Submitted"); // statuses as the badges word them (visual audit)
    expect(out).toContain("review_submitted");
    expect(out).not.toContain("<b>hi</b>");
  });
});

describe("ReviewerActions", () => {
  it("offers the review actions on a flagged claim and says the worker applies them", () => {
    const out = html(<ReviewerActions claim={flagged} onDone={() => {}} />);
    for (const a of ["approve", "deny", "request_info", "mark_fraud"]) expect(out).toContain(`data-action="${a}"`);
    expect(out).not.toContain('data-action="mark_delivered"');
    expect(out).toContain("the worker resumes it with your decision");
  });

  it("offers mark delivered on an approved replacement, and no second approval", () => {
    const out = html(<ReviewerActions claim={{ ...approved, remedy: "replacement", payout: null }} onDone={() => {}} />);
    expect(out).toContain('data-action="mark_delivered"');
    expect(out).not.toContain('data-action="approve"');
    expect(out).toContain('data-action="deny"');
  });

  it("offers nothing on a paid claim and explains why", () => {
    const out = html(<ReviewerActions claim={{ ...approved, status: "paid" }} onDone={() => {}} />);
    expect(out).not.toContain("data-action=");
    expect(out).toContain("The refund was delivered");
  });
});

describe("TraceView", () => {
  const out = renderToStaticMarkup(<TraceView trace={trace} claimId={flagged.id} cited={flagged.decision!.cited_rules} />);

  it("shows the run, its caps, and the pause for a reviewer", () => {
    expect(out).toContain("Adjudication agent");
    expect(out).toContain('data-status="waiting_human"');
    expect(out).toMatch(/data-cap="Steps"[\s\S]*?4 <span[^>]*>of 8/);
    expect(out).toContain("of 0.02");
    expect(out).toContain("paused for a reviewer");
  });

  it("renders every step with thought, tool, and expandable arguments and result", () => {
    expect(out.match(/data-step="/g)).toHaveLength(9);
    expect(out).toContain("Check the label, the purchase, duplicates");
    expect(out).toContain("<summary");
    expect(out).toContain("&quot;model_text&quot;: &quot;TL1302Z&quot;");
  });

  it("marks the steps the decision cites, with result anchors", () => {
    expect(out).toContain('id="result-T3"');
    expect(out.match(/cited by the decision/g)).toHaveLength(2);
    expect(out).toMatch(/data-step="4" data-cited="true"/);
  });

  it("shows a rejected tool call and the cap-counting steps", () => {
    expect(out).toContain("The check failed: serial: Input should be a valid string");
    expect(out.match(/counts toward the step cap/g)).toHaveLength(4);
    expect(out).toContain("Model is part of the recall (R:model).");
    expect(out).toContain("pure code");
    expect(out).not.toContain("text-[11px]");
  });

  it.each([
    ["capped", "stopped at a cap: usd cap"],
    ["failed", "The run failed: usd cap"],
    ["running", "refreshes every 1.5 seconds"],
    ["teleporting", "is not recognized"],
  ])("explains a %s run", (status, text) => {
    const t = { ...trace, run: { ...trace.run, status }, steps: [...trace.steps, { ...trace.steps[0]!, idx: 9, result: { capped: "usd cap" } }] };
    const o = renderToStaticMarkup(<TraceView trace={t as Trace} claimId="x" cited={[]} />);
    expect(o).toContain(text);
  });

  it("handles a run with no steps yet", () => {
    expect(renderToStaticMarkup(<TraceView trace={{ ...trace, steps: [] }} claimId="x" cited={[]} />)).toContain("has not written any steps yet");
  });
});

describe("ClaimDetailLive (server render)", () => {
  it("lays out a flagged claim with the reviewer banner, photos, decision, actions and fraud", () => {
    const out = html(<ClaimDetailLive initialClaim={flagged} initialTrace={trace} loadedAt={Date.now()} />);
    expect(out).toContain("Claim <span");
    expect(out).toContain("This claim needs a reviewer");
    expect(out).toContain("Label read: model TL1302Z");
    expect(out).toContain('data-action="approve"');
    expect(out).toContain("Same photo as another claim");
    expect(out).toContain(`href="/app/claims/${flagged.id}/trace"`);
    expect(out).toContain("Audit trail");
  });

  it("renders an unknown claim status without failing", () => {
    const out = html(<ClaimDetailLive initialClaim={{ ...approved, status: "archived", run: null }} initialTrace={null} loadedAt={Date.now()} />);
    expect(out).toContain("In progress");
    expect(out).toContain("is not recognized by this console");
    expect(out).toContain("Not adjudicated yet");
  });
});
