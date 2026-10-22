import { describe, expect, it } from "vitest";

import * as api from "@/lib/api-types";
import { fitWithin, isAcceptedType, needsCompression } from "@/lib/image";
import {
  EMPTY_DRAFT,
  PAYOUT_CHOOSABLE,
  STATUS_REFRESH_MAX,
  canChoosePayout,
  clearDraft,
  isPlausibleToken,
  isStripeOnboardingUrl,
  loadDraft,
  missingPhotos,
  payoutProgress,
  photoHint,
  plainStatus,
  portalPath,
  rememberConnectClaim,
  remedyPromise,
  saveDraft,
  shouldAutoRefresh,
  submitBody,
  takeConnectClaim,
  validateStep,
  type DraftFields,
} from "@/lib/portal";

const TOKEN = "dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b";
const program = { title: "Laminators", models: ["TL1302Z"], model_label_location: "on the bottom", destruction_instruction: "cut the power cord", remedy_type: "refund", required_photos: ["label", "destruction"] };
const full: DraftFields = { ...EMPTY_DRAFT, model_claimed: "TL1302Z", name: "A Claimant", email: "a@example.com", address_line1: "1 Example St", city: "Springfield", state: "IL", zip: "62701" };
const TODAY = "2026-10-20";

function memory(): Storage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, length: 0, clear: () => data.clear(), key: () => null, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) } as Storage & { data: Map<string, string> };
}

describe("tokens and paths", () => {
  it("accepts dev and opaque tokens, refuses anything that could change the path", () => {
    expect(isPlausibleToken(TOKEN)).toBe(true);
    expect(isPlausibleToken("abcDEF123_-~.xyz")).toBe(true);
    for (const bad of ["short", "../../v1/programs", "a/b/c/d/e/f", "tok en here", "x".repeat(600)]) expect(isPlausibleToken(bad)).toBe(false);
  });

  it("builds portal API paths", () => {
    expect(portalPath(TOKEN)).toBe(`/portal/${TOKEN}`);
    expect(portalPath(TOKEN, "photos")).toBe(`/portal/${TOKEN}/photos`);
  });
});

describe("validateStep", () => {
  it("requires a model number, and checks quantity", () => {
    expect(validateStep("details", EMPTY_DRAFT, [], TODAY)).toEqual({ model_claimed: "Model number is required." });
    expect(validateStep("details", { ...full, quantity: "21" }, [], TODAY).quantity).toMatch(/1 to 20/);
    expect(validateStep("details", { ...full, quantity: "1.5" }, [], TODAY).quantity).toBeDefined();
  });

  it("requires every contact field the API requires, and a plausible email", () => {
    const e = validateStep("contact", EMPTY_DRAFT, [], TODAY);
    expect(Object.keys(e).sort()).toEqual(["address_line1", "city", "email", "name", "state", "zip"]);
    expect(validateStep("contact", { ...full, email: "not-an-email" }, [], TODAY).email).toMatch(/name@example.com/);
    expect(validateStep("contact", full, [], TODAY)).toEqual({});
  });

  it("enforces the contract's length limits before the API does", () => {
    expect(validateStep("contact", { ...full, zip: "1".repeat(21) }, [], TODAY).zip).toMatch(/at most 20/);
    expect(validateStep("purchase", { ...full, retailer_claimed: "r".repeat(121) }, [], TODAY).retailer_claimed).toMatch(/at most 120/);
  });

  it("refuses a future or malformed purchase date and allows none", () => {
    expect(validateStep("purchase", { ...full, purchase_date: "2026-10-21" }, [], TODAY).purchase_date).toMatch(/future/);
    expect(validateStep("purchase", { ...full, purchase_date: "10/01/2026" }, [], TODAY).purchase_date).toBeDefined();
    expect(validateStep("purchase", { ...full, purchase_date: "" }, [], TODAY)).toEqual({});
  });

  it("blocks the photo step until every required photo is saved", () => {
    expect(validateStep("photos", full, ["destruction"], TODAY).photos).toBe("Upload the destruction photo to continue.");
    expect(validateStep("photos", full, ["label", "receipt"], TODAY).photos).toBe("Upload the label and receipt photos to continue.");
    expect(validateStep("photos", full, ["label", "destruction", "receipt"], TODAY).photos).toBe("Upload the label, destruction, and receipt photos to continue.");
    expect(validateStep("photos", full, [], TODAY)).toEqual({});
  });

  it("rechecks everything on the review step", () => {
    expect(Object.keys(validateStep("review", EMPTY_DRAFT, ["label"], TODAY))).toEqual(expect.arrayContaining(["model_claimed", "photos", "name", "zip"]));
    expect(validateStep("review", full, [], TODAY)).toEqual({});
  });

  it("lists missing photos in the required order", () => {
    expect(missingPhotos(["label", "destruction", "receipt"], ["receipt"])).toEqual(["label", "destruction"]);
  });
});

describe("submitBody", () => {
  it("trims, turns blanks into null, and passes the strict request schema", () => {
    const body = submitBody({ ...full, name: "  A Claimant ", serial_claimed: "  ", quantity: "2", purchase_date: "2026-03-01" });
    expect(body).toMatchObject({ name: "A Claimant", serial_claimed: null, quantity: 2, purchase_date: "2026-03-01", phone: null });
    expect(api.PortalSubmit.safeParse(body).success).toBe(true);
  });

  it("refuses a body the contract would reject", () => {
    expect(() => submitBody({ ...full, zip: "1".repeat(30) })).toThrow();
  });
});

describe("remedy awareness", () => {
  it("states the remedy without offering a choice", () => {
    expect(remedyPromise("refund")).toMatch(/refund/);
    expect(remedyPromise("repair")).toMatch(/repairs your product/);
    expect(remedyPromise("replacement")).toMatch(/replaces your product/);
    expect(remedyPromise("multiple")).toBe("If your claim is approved, the company tells you which remedy applies to you.");
    expect(remedyPromise(null)).toBe(remedyPromise("multiple"));
  });

  it("uses the program's label location and destruction instruction in photo hints", () => {
    expect(photoHint("label", program)).toContain("on the bottom");
    expect(photoHint("destruction", program)).toContain("First do this: cut the power cord.");
    expect(photoHint("receipt", program)).toMatch(/receipt/);
  });
});

describe("drafts", () => {
  it("saves and restores fields and step for one claim", () => {
    const s = memory();
    saveDraft(s, "c1", { ...full, serial_claimed: "S1" }, 3);
    expect(loadDraft(s, "c1")).toEqual({ fields: { ...full, serial_claimed: "S1" }, step: 3 });
    expect(loadDraft(s, "c2")).toEqual({ fields: EMPTY_DRAFT, step: 0 });
    clearDraft(s, "c1");
    expect(loadDraft(s, "c1").step).toBe(0);
  });

  it("survives corrupted, hostile, or missing storage", () => {
    const s = memory();
    s.setItem("recallops.portal.draft.c1", "{not json");
    expect(loadDraft(s, "c1")).toEqual({ fields: EMPTY_DRAFT, step: 0 });
    s.setItem("recallops.portal.draft.c1", JSON.stringify({ fields: { name: 5, __proto__: { x: 1 }, zip: "62701" }, step: 99 }));
    expect(loadDraft(s, "c1")).toEqual({ fields: { ...EMPTY_DRAFT, zip: "62701" }, step: 5 });
    const throwing = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("full"); }, removeItem: () => { throw new Error("x"); } };
    expect(loadDraft(throwing, "c1").step).toBe(0);
    expect(() => saveDraft(throwing, "c1", full, 1)).not.toThrow();
    expect(() => clearDraft(null, "c1")).not.toThrow();
  });
});

describe("status page", () => {
  const view = (status: string, payout_status: string | null = null, remedy_type: string | null = "refund") => ({ status, payout_status, program: { ...program, remedy_type } });

  it("has plain words for every claim status, and never reveals a flag", () => {
    for (const s of api.PortalViewStatusValues) expect(plainStatus(view(s)).title.length).toBeGreaterThan(5);
    for (const s of ["flagged", "capped", "under_review"]) {
      const p = plainStatus(view(s));
      expect(p.title).toBe("A person is reviewing your claim");
      expect(JSON.stringify(p)).not.toMatch(/flag|cap|fraud/i);
    }
    expect(plainStatus(view("teleported")).title).toBe("Your claim is in progress");
  });

  it("tells an approved claimant the next step for their remedy", () => {
    expect(plainStatus(view("approved", "pending")).body).toMatch(/Choose how you want to receive your refund/);
    expect(plainStatus(view("approved", null, "repair")).body).toMatch(/free repair/);
    expect(plainStatus(view("approved", null, "replacement")).body).toMatch(/free replacement/);
    expect(plainStatus(view("approved", null, "multiple")).body).toBe("The company will contact you about your remedy.");
  });

  it("offers the payout choice only after approval and only while it can change", () => {
    for (const p of PAYOUT_CHOOSABLE) expect(canChoosePayout({ status: "approved", payout_status: p })).toBe(true);
    for (const p of ["transferring", "transferred", "exported", "held", null]) expect(canChoosePayout({ status: "approved", payout_status: p })).toBe(false);
    for (const s of ["submitted", "needs_info", "under_review", "flagged", "denied", "paid"]) expect(canChoosePayout({ status: s, payout_status: "pending" })).toBe(false);
  });

  it("describes every payout status, unknown ones included", () => {
    for (const p of api.PortalViewPayoutStatusValues) expect(payoutProgress(p)).toBeTruthy();
    expect(payoutProgress("held")).toMatch(/on hold while a person reviews/);
    expect(payoutProgress("teleporting")).toBe("Your refund is being processed.");
    expect(payoutProgress(null)).toBeNull();
  });

  it("refreshes a submitted claim at most a few times", () => {
    expect(shouldAutoRefresh("submitted", 0)).toBe(true);
    expect(shouldAutoRefresh("submitted", STATUS_REFRESH_MAX)).toBe(false);
    expect(shouldAutoRefresh("approved", 0)).toBe(false);
    expect(STATUS_REFRESH_MAX * 1).toBeLessThanOrEqual(10);
  });
});

describe("Stripe onboarding handoff", () => {
  it("only follows https links on stripe.com", () => {
    expect(isStripeOnboardingUrl("https://connect.stripe.com/setup/e/acct_1/abc")).toBe(true);
    for (const bad of ["http://connect.stripe.com/x", "https://stripe.com.evil.example/x", "javascript:alert(1)", "https://evil.example/stripe.com", "", null]) {
      expect(isStripeOnboardingUrl(bad)).toBe(false);
    }
  });

  it("remembers which claim went to Stripe exactly once", () => {
    const s = memory();
    rememberConnectClaim(s, TOKEN);
    expect(takeConnectClaim(s)).toBe(TOKEN);
    expect(takeConnectClaim(s)).toBeNull();
    s.setItem("recallops.portal.connect", "../../v1");
    expect(takeConnectClaim(s)).toBeNull();
  });
});

describe("image sizing", () => {
  it("fits within the longest edge without upscaling", () => {
    expect(fitWithin(4000, 3000)).toEqual({ width: 2000, height: 1500 });
    expect(fitWithin(3000, 4000)).toEqual({ width: 1500, height: 2000 });
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
    expect(fitWithin(0, 10)).toEqual({ width: 0, height: 0 });
  });

  it("compresses only what is too heavy or too large", () => {
    expect(needsCompression(1_400_000, 1800, 1200)).toBe(false);
    expect(needsCompression(1_600_000, 1800, 1200)).toBe(true);
    expect(needsCompression(200_000, 4032, 3024)).toBe(true);
  });

  it("accepts only the types the API accepts", () => {
    expect(isAcceptedType("image/jpeg") && isAcceptedType("image/png")).toBe(true);
    expect(isAcceptedType("image/heic")).toBe(false);
  });
});

describe("review step on phones", () => {
  it("lets the value column shrink below a long email", async () => {
    const src = (await import("node:fs")).readFileSync(new URL("../src/components/portal/ClaimStepper.tsx", import.meta.url), "utf8");
    expect(src).toContain("grid-cols-[7.5rem_minmax(0,1fr)]");
    expect(src).toContain("[overflow-wrap:anywhere]");
  });
});
