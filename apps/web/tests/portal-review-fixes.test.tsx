// Claim portal review fixes: payout choice for a scheduled check, blocked site data, reply photo gating, stalled
// uploads, and the model number when a notice lists no models.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import { StepNav } from "@/components/portal/ClaimStepper";
import { AnswerActions, NeedsInfoReply } from "@/components/portal/NeedsInfoReply";
import { PayoutChoice } from "@/components/portal/PayoutChoice";
import { PHOTO_UPLOAD_TIMEOUT_MS, uploadPhoto } from "@/components/portal/PhotoCapture";
import { ApiError } from "@/lib/api/problem";
import {
  EMPTY_DRAFT,
  anyPhotoBusy,
  answerGate,
  handOffToStripe,
  modelRequired,
  offersBankTransfer,
  payoutSentence,
  sessionStore,
  validateStep,
  type DraftFields,
} from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

const T = "dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b";
const TODAY = "2026-10-20";
const STRIPE_URL = "https://connect.stripe.com/setup/e/acct_123/abc";
const program = { title: "Laminators", models: ["TL1302Z"], model_label_location: "on the bottom", destruction_instruction: "cut the power cord", remedy_type: "refund", required_photos: ["label", "destruction"] };
const full: DraftFields = { ...EMPTY_DRAFT, model_claimed: "TL1302Z", name: "A Claimant", email: "a@example.com", address_line1: "1 Example St", city: "Springfield", state: "IL", zip: "62701" };

/** The opening tag of the first element carrying `marker`. */
function tag(html: string, marker: string): string {
  const at = html.indexOf(marker);
  expect(at, `${marker} not rendered`).toBeGreaterThan(-1);
  return html.slice(html.lastIndexOf("<", at), html.indexOf(">", at) + 1);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("payout choice for a scheduled check (finding 1)", () => {
  it("offers no bank transfer once the payout is pending_export, only the address update", () => {
    const out = renderToStaticMarkup(<PayoutChoice token={T} current="pending_export" />);
    expect(out).not.toContain("Stripe");
    expect(out).not.toContain('type="radio"');
    expect(out).toContain("A check is already scheduled for this refund, so a bank transfer is not available.");
    expect(out).toContain("New mailing address");
    expect(out).toContain("Update mailing address");
    // An empty address would change nothing, so the button waits for one.
    expect(tag(out, "data-payout-submit")).toContain('disabled=""');
  });

  it("still offers the bank transfer while Stripe onboarding can start", () => {
    for (const current of ["pending", "onboarding", "failed"]) {
      const out = renderToStaticMarkup(<PayoutChoice token={T} current={current} />);
      expect(out).toContain("A bank transfer (set up on Stripe");
      expect(out).toContain("How do you want to receive your refund?");
      expect(tag(out, "data-payout-submit")).not.toContain('disabled=""');
    }
  });

  it("matches the API: Connect onboarding starts only from pending, onboarding, or failed", () => {
    expect(["pending", "onboarding", "failed"].map(offersBankTransfer)).toEqual([true, true, true]);
    for (const s of ["pending_export", "held", "transferring", "exported", "transferred", null, undefined]) expect(offersBankTransfer(s)).toBe(false);
  });

  it("gives a payout 409 its own sentence and leaves the rest to the portal copy", () => {
    expect(payoutSentence(409)).toBe("Your refund can no longer be changed this way. Refresh the page to see how it will be paid.");
    expect(payoutSentence(409)).not.toBe(portalSentence(409));
    for (const s of [0, 400, 402, 404, 422, 429, 503]) expect(payoutSentence(s)).toBe(portalSentence(s));
  });
});

describe("Stripe hand-off with blocked site data (finding 2)", () => {
  const blocked = () =>
    Object.defineProperty({}, "sessionStorage", {
      get() {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });

  it("still sends the claimant to Stripe when reading sessionStorage throws", () => {
    vi.stubGlobal("window", blocked());
    const assign = vi.fn();
    expect(sessionStore()).toBeNull();
    expect(() => handOffToStripe(STRIPE_URL, T, assign)).not.toThrow();
    expect(assign).toHaveBeenCalledWith(STRIPE_URL);
  });

  it("remembers the claim when storage works", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("window", { sessionStorage: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), removeItem: (k: string) => void data.delete(k) } });
    const assign = vi.fn();
    handOffToStripe(STRIPE_URL, T, assign);
    expect(data.get("recallops.portal.connect")).toBe(T);
    expect(assign).toHaveBeenCalledWith(STRIPE_URL);
  });
});

describe("needs_info reply waits for its photo (finding 3)", () => {
  it("blocks sending while the photo is preparing or uploading, with a reason", () => {
    for (const p of ["preparing", "uploading"] as const) {
      expect(answerGate(p)).toEqual({ canSend: false, reason: "Wait for the photo to finish uploading, then send your answer.", offerWithoutPhoto: false });
      expect(answerGate(p, true).canSend).toBe(false);
    }
  });

  it("after a failed photo requires Try again or an explicit send without it", () => {
    expect(answerGate("error")).toMatchObject({ canSend: false, offerWithoutPhoto: true, reason: expect.stringContaining("Try again") });
    expect(answerGate("error", true)).toEqual({ canSend: true, reason: null, offerWithoutPhoto: false });
  });

  it("sends freely with no photo, an unused photo slot, or a saved photo", () => {
    for (const p of [null, "idle", "done"] as const) expect(answerGate(p).canSend).toBe(true);
  });

  it("renders Send answer disabled with the reason while uploading", () => {
    const out = renderToStaticMarkup(<AnswerActions gate={answerGate("uploading")} busy={false} onSendWithoutPhoto={() => {}} />);
    expect(tag(out, "data-send-answer")).toContain('disabled=""');
    expect(tag(out, "data-send-answer")).toContain('aria-describedby="reply-send-reason"');
    expect(out).toContain("Wait for the photo to finish uploading, then send your answer.");
    expect(out).not.toContain("without the photo");
  });

  it("renders the send-without-photo choice after a failed photo", () => {
    const out = renderToStaticMarkup(<AnswerActions gate={answerGate("error")} busy={false} onSendWithoutPhoto={() => {}} />);
    expect(tag(out, "data-send-answer")).toContain('disabled=""');
    expect(out).toContain("Send my answer without the photo");
  });

  it("starts with Send answer enabled when no photo is chosen", () => {
    const out = renderToStaticMarkup(<NeedsInfoReply token={T} view={{ program, answer_fields: [], answer_photos: [] }} />);
    expect(tag(out, "data-send-answer")).not.toContain('disabled=""');
    expect(out).not.toContain("data-send-blocked");
  });
});

describe("claim form waits for photos in flight (finding 3)", () => {
  const nav = (over: Partial<Parameters<typeof StepNav>[0]>) =>
    renderToStaticMarkup(<StepNav showBack isReview={false} submitting={false} photosBusy={false} onBack={() => {}} onContinue={() => {}} onSubmit={() => {}} {...over} />);

  it("disables Continue and Back while a photo is uploading", () => {
    const out = nav({ photosBusy: true });
    expect(tag(out, "data-continue")).toContain('disabled=""');
    expect(tag(out, "data-back")).toContain('disabled=""');
    expect(out).toContain("Wait for the photo to finish uploading.");
  });

  it("disables Submit while a photo is uploading", () => {
    expect(tag(nav({ isReview: true, photosBusy: true }), "data-submit")).toContain('disabled=""');
  });

  it("leaves the buttons enabled when no photo is on its way", () => {
    const out = nav({});
    expect(tag(out, "data-continue")).not.toContain('disabled=""');
    expect(tag(out, "data-back")).not.toContain('disabled=""');
    expect(out).not.toContain("data-photos-busy");
  });

  it("counts any photo still preparing or uploading as busy", () => {
    expect(anyPhotoBusy({})).toBe(false);
    expect(anyPhotoBusy({ label: "done", destruction: "error" })).toBe(false);
    expect(anyPhotoBusy({ label: "done", destruction: "uploading" })).toBe(true);
    expect(anyPhotoBusy({ receipt: "preparing" })).toBe(true);
  });
});

describe("stalled photo upload (finding 4)", () => {
  it("stops after about 45 seconds by default", () => {
    expect(PHOTO_UPLOAD_TIMEOUT_MS).toBe(45_000);
  });

  it("turns a stalled upload into a status 0 problem so Try again can resend", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
      signal = init.signal ?? undefined;
      return new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason)));
    });
    const err = await uploadPhoto(T, "label", new Blob(["x"], { type: "image/jpeg" }), 20).catch((e: unknown) => e);
    expect(signal).toBeDefined();
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).problem).toMatchObject({ status: 0, detail: expect.stringContaining("took too long") });
  });

  it("passes other API errors through unchanged", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ title: "Payload Too Large", status: 413, detail: "photo is larger than 8 MB" }, { status: 413 }));
    const err = await uploadPhoto(T, "label", new Blob(["x"], { type: "image/jpeg" }), 1_000).catch((e: unknown) => e);
    expect((err as ApiError).problem).toMatchObject({ status: 413, detail: "photo is larger than 8 MB" });
  });

  it("sends the photo to the claim's photo route", async () => {
    let seen: { url: string; kind: unknown } | null = null;
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen = { url, kind: (init.body as FormData).get("kind") };
      return Response.json({ id: "p1", kind: "label", phash: "0", sha256: "0", uploaded_at: "2026-10-20T08:00:00Z", url: "/x" });
    });
    await expect(uploadPhoto(T, "label", new Blob(["x"], { type: "image/jpeg" }))).resolves.toBeUndefined();
    expect(seen).toEqual({ url: `/api/portal/${encodeURIComponent(T)}/photos`, kind: "label" });
  });
});

describe("model number only when the notice lists models (finding 5)", () => {
  it("does not require a model when the recall lists none", () => {
    expect(modelRequired({ models: [] })).toBe(false);
    expect(validateStep("details", EMPTY_DRAFT, [], TODAY, { requireModel: false })).toEqual({});
    expect(validateStep("review", { ...full, model_claimed: "" }, [], TODAY, { requireModel: false })).toEqual({});
  });

  it("still requires it when the recall lists models, and by default", () => {
    expect(modelRequired({ models: ["TL1302Z"] })).toBe(true);
    expect(validateStep("details", EMPTY_DRAFT, [], TODAY, { requireModel: true })).toEqual({ model_claimed: "Model number is required." });
    expect(validateStep("details", EMPTY_DRAFT, [], TODAY)).toEqual({ model_claimed: "Model number is required." });
  });

  it("still enforces the model's length limit when it is optional", () => {
    expect(validateStep("details", { ...full, model_claimed: "m".repeat(81) }, [], TODAY, { requireModel: false }).model_claimed).toMatch(/at most 80/);
  });
});
