import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ClaimStepper } from "@/components/portal/ClaimStepper";
import { PhotoCapture } from "@/components/portal/PhotoCapture";
import { StatusView } from "@/components/portal/StatusView";
import { PortalView } from "@/lib/api-types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

const base = PortalView.parse(JSON.parse(readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8")));
const T = "dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b";
const status = (over: Partial<PortalView>) => renderToStaticMarkup(<StatusView token={T} view={{ ...base, ...over }} />);

describe("StatusView", () => {
  it("asks the needs_info question with a reply form and an optional photo", () => {
    const out = status({ status: "needs_info", pending_question: "Please photograph the label again.", decision_reasons: ["More information is needed to finish your claim."] });
    expect(out).toContain("We need more information");
    expect(out).toContain("Please photograph the label again.");
    expect(out).toContain('<label for="reply"');
    expect(out).toContain("Add a photo (optional)");
    expect(out).not.toContain("How do you want to receive your refund?");
  });

  it("offers the payout choice on an approved refund with a choosable payout", () => {
    const out = status({ status: "approved", payout_status: "pending" });
    expect(out).toContain("Your claim was approved");
    expect(out).toContain("How do you want to receive your refund?");
    expect(out).toContain("A mailed check");
    expect(out).toContain("Stripe");
  });

  it("shows progress, not a choice, once money is moving or held", () => {
    for (const [p, text] of [["transferring", "on its way"], ["held", "on hold while a person reviews"], ["exported", "check was issued"]] as const) {
      const out = status({ status: p === "held" ? "under_review" : "approved", payout_status: p });
      expect(out).toContain(text);
      expect(out).not.toContain("How do you want to receive your refund?");
    }
  });

  it("is remedy-aware for an approved repair with no payout", () => {
    const out = status({ status: "approved", payout_status: null, program: { ...base.program, remedy_type: "repair" } });
    expect(out).toContain("arrange your free repair");
    expect(out).not.toContain("Your refund");
  });

  it("never offers payout before approval", () => {
    for (const s of ["submitted", "flagged", "needs_info", "denied"]) {
      expect(status({ status: s, payout_status: "pending" })).not.toContain("How do you want to receive your refund?");
    }
  });

  it("shows denial reasons in plain words", () => {
    const out = status({ status: "denied", decision_reasons: ["The model number is not part of this recall."] });
    expect(out).toContain("Your claim was not approved");
    expect(out).toContain("The model number is not part of this recall.");
  });

  it("does not tell the claimant a claim was flagged", () => {
    const out = status({ status: "flagged" });
    expect(out).toContain("A person is reviewing your claim");
    expect(out).not.toMatch(/flag/i);
  });

  it("auto-refreshes only while the claim is being checked", () => {
    expect(status({ status: "submitted" })).toContain("checks for updates every 15 seconds");
    expect(status({ status: "approved", payout_status: "pending" })).not.toContain("checks for updates");
  });

  it("shows the message thread, escaped", () => {
    const out = status({ status: "submitted", messages: [{ sender: "firm", text: "<b>Hi</b>", at: "2026-10-20T08:00:00Z" }, { sender: "claimant", text: "Here it is", at: "2026-10-20T08:05:00Z" }] });
    expect(out).toContain("The firm");
    expect(out).toContain("You ·");
    expect(out).not.toContain("<b>Hi</b>");
  });
});

describe("ClaimStepper (first render)", () => {
  const out = renderToStaticMarkup(<ClaimStepper token={T} view={base} />);

  it("starts on product confirmation with the models, label location, and remedy", () => {
    expect(out).toContain("Step 1 of 6");
    expect(out).toContain('data-step="product"');
    expect(out).toContain("TL1302Z");
    expect(out).toContain("on the bottom of the laminator");
    expect(out).toContain("If your claim is approved, you receive a refund.");
  });

  it("offers no remedy selection and no payout step", () => {
    expect(out).not.toContain("<select");
    expect(out).not.toContain('type="radio"');
    expect(out).not.toMatch(/receive your refund\?/);
  });

  it("uses large tap targets", () => {
    expect(out).toContain("min-h-12");
  });
});

describe("PhotoCapture", () => {
  it("is a camera-first JPEG or PNG input with the hint", () => {
    const out = renderToStaticMarkup(<PhotoCapture token={T} kind="label" hint="It is on the bottom." alreadyUploaded={false} onUploaded={() => {}} />);
    expect(out).toContain('accept="image/jpeg,image/png"');
    expect(out).toContain('capture="environment"');
    expect(out).toContain("Take or choose a photo");
    expect(out).toContain("It is on the bottom.");
    expect(out).toContain('aria-live="polite"');
  });

  it("shows an uploaded photo as saved, with retake", () => {
    const out = renderToStaticMarkup(<PhotoCapture token={T} kind="destruction" hint="" alreadyUploaded onUploaded={() => {}} />);
    expect(out).toContain("Destruction photo saved.");
    expect(out).toContain("Retake photo");
  });
});
