// Claim portal rules (02-ui.md /claim/[token]): steps, validation, resumable drafts, plain-language status, and the
// post-approval payout choice. Pure, so it is unit-tested. The backend stays the authority on every rule here.
import { PortalSubmit, type PortalView } from "@/lib/api-types";
import { portalSentence } from "@/lib/portal-copy";
import { isStripeHostedUrl } from "@/lib/stripe-url";

/** Claim links are opaque bearer tokens (dev.<firm>.<claim> today, HMAC from component 4 later). */
const TOKEN = /^[A-Za-z0-9._~-]{8,512}$/;

export function isPlausibleToken(token: string): boolean {
  return TOKEN.test(token);
}

export function portalPath(token: string, action = ""): string {
  return `/portal/${encodeURIComponent(token)}${action ? `/${action}` : ""}`;
}

export const STEPS = [
  { id: "product", title: "Confirm your product" },
  { id: "details", title: "Model and codes" },
  { id: "photos", title: "Photos" },
  { id: "purchase", title: "Purchase" },
  { id: "contact", title: "Your details" },
  { id: "review", title: "Review and submit" },
] as const;
export type StepId = (typeof STEPS)[number]["id"];

export type DraftFields = {
  model_claimed: string;
  serial_claimed: string;
  date_code_claimed: string;
  quantity: string;
  retailer_claimed: string;
  purchase_date: string;
  name: string;
  email: string;
  phone: string;
  address_line1: string;
  city: string;
  state: string;
  zip: string;
};

export const EMPTY_DRAFT: DraftFields = {
  model_claimed: "",
  serial_claimed: "",
  date_code_claimed: "",
  quantity: "1",
  retailer_claimed: "",
  purchase_date: "",
  name: "",
  email: "",
  phone: "",
  address_line1: "",
  city: "",
  state: "",
  zip: "",
};

export type FieldErrors = Partial<Record<keyof DraftFields | "photos", string>>;

// Limits from the PortalSubmit contract (maxLength), so errors appear here instead of as a 422.
const MAX: Record<keyof DraftFields, number> = {
  model_claimed: 80,
  serial_claimed: 80,
  date_code_claimed: 40,
  quantity: 2,
  retailer_claimed: 120,
  purchase_date: 10,
  name: 120,
  email: 200,
  phone: 40,
  address_line1: 200,
  city: 100,
  state: 40,
  zip: 20,
};

const REQUIRED_BY_STEP: Partial<Record<StepId, ReadonlyArray<keyof DraftFields>>> = {
  details: ["model_claimed"],
  contact: ["name", "email", "address_line1", "city", "state", "zip"],
};

const LABEL: Record<keyof DraftFields, string> = {
  model_claimed: "Model number",
  serial_claimed: "Serial number",
  date_code_claimed: "Date code",
  quantity: "How many you have",
  retailer_claimed: "Where you bought it",
  purchase_date: "Purchase date",
  name: "Full name",
  email: "Email",
  phone: "Phone",
  address_line1: "Street address",
  city: "City",
  state: "State",
  zip: "ZIP code",
};

export function fieldLabel(k: keyof DraftFields): string {
  return LABEL[k];
}

const STEP_FIELDS: Record<StepId, ReadonlyArray<keyof DraftFields>> = {
  product: [],
  details: ["model_claimed", "serial_claimed", "date_code_claimed", "quantity"],
  photos: [],
  purchase: ["retailer_claimed", "purchase_date"],
  contact: ["name", "email", "phone", "address_line1", "city", "state", "zip"],
  review: [],
};

/**
 * The model number is required only when the notice lists models to match it against; the API accepts a claim
 * without one (PortalSubmit.model_claimed is optional), and a recall with no listed models gives nothing to type.
 */
export function modelRequired(program: Pick<PortalView["program"], "models">): boolean {
  return program.models.length > 0;
}

export type ValidateOptions = { requireModel?: boolean };

export function validateStep(step: StepId, f: DraftFields, missingPhotos: readonly string[], today: string, opts: ValidateOptions = {}): FieldErrors {
  const errors: FieldErrors = {};
  const requireModel = opts.requireModel ?? true;
  const required = (REQUIRED_BY_STEP[step] ?? []).filter((k) => requireModel || k !== "model_claimed");
  for (const k of STEP_FIELDS[step]) {
    const v = f[k].trim();
    if (required.includes(k) && !v) errors[k] = `${LABEL[k]} is required.`;
    else if (v.length > MAX[k]) errors[k] = `${LABEL[k]} must be at most ${MAX[k]} characters.`;
  }
  if (step === "details") {
    const q = Number(f.quantity);
    if (!Number.isInteger(q) || q < 1 || q > 20) errors.quantity = "Enter a whole number from 1 to 20.";
  }
  if (step === "purchase" && f.purchase_date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.purchase_date) || Number.isNaN(Date.parse(`${f.purchase_date}T00:00:00Z`))) {
      errors.purchase_date = "Enter the date as year, month, and day.";
    } else if (f.purchase_date > today) {
      errors.purchase_date = "The purchase date cannot be in the future.";
    }
  }
  if (step === "contact" && f.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email.trim())) {
    errors.email = "Enter an email address like name@example.com.";
  }
  if (step === "photos" && missingPhotos.length) {
    errors.photos = `Upload the ${listJoin(missingPhotos.map(photoKindLabel))} photo${missingPhotos.length > 1 ? "s" : ""} to continue.`;
  }
  if (step === "review") {
    for (const s of ["details", "photos", "purchase", "contact"] as const) Object.assign(errors, validateStep(s, f, missingPhotos, today, opts));
  }
  return errors;
}

/** The submit body, checked against the strict request schema before it is sent. */
export function submitBody(f: DraftFields): PortalSubmit {
  const opt = (v: string) => (v.trim() ? v.trim() : null);
  return PortalSubmit.parse({
    name: f.name.trim(),
    email: f.email.trim(),
    phone: opt(f.phone),
    address_line1: f.address_line1.trim(),
    city: f.city.trim(),
    state: f.state.trim(),
    zip: f.zip.trim(),
    model_claimed: opt(f.model_claimed),
    serial_claimed: opt(f.serial_claimed),
    date_code_claimed: opt(f.date_code_claimed),
    retailer_claimed: opt(f.retailer_claimed),
    purchase_date: opt(f.purchase_date),
    quantity: Number(f.quantity) || 1,
  });
}

/** "a", "a and b", "a, b, and c". */
export function listJoin(items: readonly string[]): string {
  if (items.length <= 2) return items.join(" and ");
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}

export function missingPhotos(required: readonly string[], uploaded: readonly string[]): string[] {
  return required.filter((k) => !uploaded.includes(k));
}

const PHOTO_LABEL: Record<string, string> = { label: "label", destruction: "destruction", receipt: "receipt", other: "extra" };
export function photoKindLabel(kind: string): string {
  return Object.hasOwn(PHOTO_LABEL, kind) ? PHOTO_LABEL[kind]! : kind;
}

// ---- Photo uploads ----

/** One PhotoCapture's state; preparing and uploading mean the photo is not on the server yet. */
export type PhotoPhase = "idle" | "preparing" | "uploading" | "done" | "error";

export function isPhotoBusy(phase: PhotoPhase | null | undefined): boolean {
  return phase === "preparing" || phase === "uploading";
}

/** True while any photo on the page is still being prepared or uploaded, so nothing may move on without it. */
export function anyPhotoBusy(phases: Readonly<Record<string, PhotoPhase>>): boolean {
  return Object.values(phases).some(isPhotoBusy);
}

export const PHOTOS_BUSY_REASON = "Wait for the photo to finish uploading.";

export type AnswerGate = { canSend: boolean; reason: string | null; offerWithoutPhoto: boolean };

/**
 * Whether a needs_info answer may be sent given the reply photo's state. The answer starts a new check of the
 * claim, so a photo still on its way would arrive after that check began or not at all; a photo that failed needs
 * Try again or an explicit choice to send without it.
 */
export function answerGate(photo: PhotoPhase | null, sendWithoutPhoto = false): AnswerGate {
  if (isPhotoBusy(photo)) {
    return { canSend: false, reason: "Wait for the photo to finish uploading, then send your answer.", offerWithoutPhoto: false };
  }
  if (photo === "error" && !sendWithoutPhoto) {
    return { canSend: false, reason: "Your photo did not upload. Use Try again on the photo, or send your answer without it.", offerWithoutPhoto: true };
  }
  return { canSend: true, reason: null, offerWithoutPhoto: false };
}

export function photoHint(kind: string, program: PortalView["program"]): string {
  if (kind === "label") {
    return program.model_label_location
      ? `Photograph the label with the model number. It is ${program.model_label_location}.`
      : "Photograph the label that shows the model number.";
  }
  if (kind === "destruction") {
    return program.destruction_instruction
      ? `First do this: ${program.destruction_instruction}. Then photograph the product so the result is visible.`
      : "Photograph the product after disabling it as the recall notice instructs.";
  }
  if (kind === "receipt") return "Photograph your receipt or order confirmation so the store and date are readable.";
  return "Add a photo.";
}

/** The remedy as the claimant should read it; never a choice, because the backend assigns the remedy. */
export function remedyPromise(remedyType: string | null | undefined): string {
  switch (remedyType) {
    case "refund":
      return "If your claim is approved, you receive a refund.";
    case "repair":
      return "If your claim is approved, the firm repairs your product for free.";
    case "replacement":
      return "If your claim is approved, the firm replaces your product for free.";
    default:
      return "If your claim is approved, the firm tells you which remedy applies to you.";
  }
}

// ---- Resumable drafts (fields only; photos are saved server-side as soon as they upload) ----

export type DraftStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const draftKey = (claimId: string) => `recallops.portal.draft.${claimId}`;

export function loadDraft(store: DraftStore | null, claimId: string): { fields: DraftFields; step: number } {
  try {
    const raw = store?.getItem(draftKey(claimId));
    if (!raw) return { fields: { ...EMPTY_DRAFT }, step: 0 };
    const parsed = JSON.parse(raw) as { fields?: Record<string, unknown>; step?: unknown };
    const fields = { ...EMPTY_DRAFT };
    for (const k of Object.keys(EMPTY_DRAFT) as Array<keyof DraftFields>) {
      const v = parsed.fields?.[k];
      if (typeof v === "string") fields[k] = v.slice(0, MAX[k] + 20);
    }
    const step = typeof parsed.step === "number" && Number.isInteger(parsed.step) ? Math.min(Math.max(parsed.step, 0), STEPS.length - 1) : 0;
    return { fields, step };
  } catch {
    return { fields: { ...EMPTY_DRAFT }, step: 0 };
  }
}

export function saveDraft(store: DraftStore | null, claimId: string, fields: DraftFields, step: number): void {
  try {
    store?.setItem(draftKey(claimId), JSON.stringify({ fields, step }));
  } catch {
    // Private mode or a full store: the form still works, it just does not survive a reload.
  }
}

export function clearDraft(store: DraftStore | null, claimId: string): void {
  try {
    store?.removeItem(draftKey(claimId));
  } catch {
    // nothing to clear
  }
}

// ---- Status page ----

export type PlainStatus = { title: string; body: string; tone: "neutral" | "good" | "attention" };

/** Plain words for the claimant. Flagged and capped read as "a person is reviewing": the claimant never sees why. */
export function plainStatus(view: Pick<PortalView, "status" | "program" | "payout_status">): PlainStatus {
  switch (view.status) {
    case "draft":
      return { title: "Your claim is not submitted yet", body: "Finish the steps to submit it.", tone: "neutral" };
    case "submitted":
      return { title: "We received your claim", body: "We are checking it now. This usually takes a few minutes.", tone: "neutral" };
    case "needs_info":
      return { title: "We need more information", body: "Answer the question below so we can finish your claim.", tone: "attention" };
    case "under_review":
    case "flagged":
    case "capped":
      return { title: "A person is reviewing your claim", body: "You do not need to do anything now. We will update this page.", tone: "neutral" };
    case "approved":
      return { title: "Your claim was approved", body: approvedNextStep(view.program.remedy_type, view.payout_status), tone: "good" };
    case "denied":
      return { title: "Your claim was not approved", body: "The reasons are below. If you think this is wrong, contact the firm that sent you this link.", tone: "attention" };
    case "paid":
      return { title: "Your refund was sent", body: "Your claim is complete.", tone: "good" };
    case "closed":
      return { title: "Your remedy was delivered", body: "Your claim is complete.", tone: "good" };
    default:
      return { title: "Your claim is in progress", body: "We will update this page as it moves forward.", tone: "neutral" };
  }
}

function approvedNextStep(remedyType: string | null | undefined, payoutStatus: string | null | undefined): string {
  if (payoutStatus) return "Choose how you want to receive your refund, or check its progress below.";
  if (remedyType === "repair") return "The firm will contact you to arrange your free repair.";
  if (remedyType === "replacement") return "The firm will contact you to arrange your free replacement.";
  return "The firm will contact you about your remedy.";
}

/** Payout states in which the claimant may still choose or change the destination (portal.py CHOOSABLE). */
export const PAYOUT_CHOOSABLE = ["pending", "onboarding", "failed", "pending_export"] as const;

export function canChoosePayout(view: Pick<PortalView, "status" | "payout_status">): boolean {
  return view.status === "approved" && !!view.payout_status && (PAYOUT_CHOOSABLE as readonly string[]).includes(view.payout_status);
}

export function payoutProgress(status: string | null | undefined): string | null {
  switch (status) {
    case null:
    case undefined:
      return null;
    case "pending":
      return "Choose how you want to receive your refund.";
    case "pending_export":
      return "A check will be mailed to you. You can still change the mailing address.";
    case "onboarding":
      return "Finish setting up your bank transfer with Stripe to receive your refund.";
    case "failed":
      return "The last transfer did not go through. Choose how to receive your refund again.";
    case "transferring":
      return "Your refund is on its way.";
    case "transferred":
      return "Your refund was sent to your Stripe account.";
    case "exported":
      return "Your check was issued.";
    case "held":
      return "Your refund is on hold while a person reviews it.";
    default:
      return "Your refund is being processed.";
  }
}

/** Payout states from which Stripe Connect onboarding may start (stripe_connect.py onboarding); pending_export is not one. */
export const CONNECT_CHOOSABLE = ["pending", "onboarding", "failed"] as const;

/** A scheduled check (pending_export) can only be exported or held, so a bank transfer is offered only before that. */
export function offersBankTransfer(current: string | null | undefined): boolean {
  return !!current && (CONNECT_CHOOSABLE as readonly string[]).includes(current);
}

/** A 409 from the payout call means the refund moved on while the page was open; say that, not "your claim changed". */
export function payoutSentence(status: number): string {
  if (status === 409) return "Your refund can no longer be changed this way. Refresh the page to see how it will be paid.";
  return portalSentence(status);
}

/** After a payout error: 402 means the firm's plan pays refunds by mailed check only, so the form switches to it. */
export function methodAfterPayoutError(status: number, method: "check_export" | "stripe_connect"): "check_export" | "stripe_connect" {
  return status === 402 ? "check_export" : method;
}

/** Only a Stripe-hosted https page may receive the claimant (Account Links are ephemeral bearer URLs). */
export function isStripeOnboardingUrl(url: string | null | undefined): url is string {
  return isStripeHostedUrl(url);
}

/** This tab's sessionStorage, or null when the browser blocks site data (reading the property itself can throw). */
export function sessionStore(): DraftStore | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** The Connect return URL is one fixed setting, so the tab remembers which claim went to Stripe. */
const RETURN_KEY = "recallops.portal.connect";

export function rememberConnectClaim(store: DraftStore | null, token: string): void {
  try {
    store?.setItem(RETURN_KEY, token);
  } catch {
    // without storage the claimant reopens their link
  }
}

/**
 * Sends the claimant to Stripe's onboarding page. Remembering the claim is best effort: with site data blocked the
 * claimant still reaches Stripe and reopens their link afterwards.
 */
export function handOffToStripe(url: string, token: string, assign: (url: string) => void): void {
  rememberConnectClaim(sessionStore(), token);
  assign(url);
}

export function takeConnectClaim(store: DraftStore | null): string | null {
  try {
    const t = store?.getItem(RETURN_KEY) ?? null;
    store?.removeItem(RETURN_KEY);
    return t && isPlausibleToken(t) ? t : null;
  } catch {
    return null;
  }
}

/** Polling budget for the status page: 60 portal requests per link per hour (guards.py), so at most 8 refreshes. */
export const STATUS_REFRESH_MS = 15_000;
export const STATUS_REFRESH_MAX = 8;

export function shouldAutoRefresh(status: string, refreshesSoFar: number): boolean {
  return status === "submitted" && refreshesSoFar < STATUS_REFRESH_MAX;
}
