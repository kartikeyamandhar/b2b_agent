// Claim portal rules (02-ui.md /claim/[token]): steps, validation, resumable drafts, plain-language status, the
// post-approval payout choice, and what this device remembers about the claims it started. Pure, so it is
// unit-tested, and safe for client components (no server imports). The backend stays the authority on every rule.
import { PortalSubmit, type PortalAnswer, type PortalAnswerField, type PortalView } from "@/lib/api-types";
import type { ApiProblem } from "@/lib/api/problem";
import { portalSentence } from "@/lib/portal-copy";
import { isStripeHostedUrl } from "@/lib/stripe-url";

/** Claim links are opaque bearer tokens (dev.<firm>.<claim> in development, 96 characters of HMAC otherwise). */
const TOKEN = /^[A-Za-z0-9._~-]{8,512}$/;

export function isPlausibleToken(token: string): boolean {
  return TOKEN.test(token);
}

export function portalPath(token: string, action = ""): string {
  return `/portal/${encodeURIComponent(token)}${action ? `/${action}` : ""}`;
}

/** The claimant's private claim page, and its full address for copying, sharing, and the QR code. */
export function claimPagePath(token: string): string {
  return `/claim/${encodeURIComponent(token)}`;
}

export function claimUrl(origin: string, token: string): string {
  return `${origin.replace(/\/+$/, "")}${claimPagePath(token)}`;
}

/** The API route behind a product's public claim link (plan v3): GET shows the product, POST starts a claim. */
export function intakePath(token: string): string {
  return `/portal/intake/${encodeURIComponent(token)}`;
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

/** The photos a claim form asks for: the program's, with the label photo always first, because the API refuses to
 *  submit any claim without one (plan v3). */
export function claimPhotoKinds(required: readonly string[]): string[] {
  return ["label", ...required.filter((k) => k !== "label")];
}

export const LABEL_PHOTO_REASON = "Add a photo of the product's label before you submit.";

/** Why Submit is disabled, or null when it may be pressed: the label photo must be on the server first. */
export function submitBlockedReason(uploaded: readonly string[]): string | null {
  return uploaded.includes("label") ? null : LABEL_PHOTO_REASON;
}

/** A failed submit in words. The API answers 409 only while the label photo is missing, and its own sentence says so
 *  ("add a photo of the product's label before submitting"); anything else gets the portal sentence. */
export function submitSentence(problem: Pick<ApiProblem, "status" | "detail">): string {
  const detail = problem.detail?.trim();
  if (problem.status === 409 && detail && /label/i.test(detail)) {
    const s = capitalize(detail);
    return /[.!?]$/.test(s) ? s : `${s}.`;
  }
  return portalSentence(problem.status);
}

const PHOTO_LABEL: Record<string, string> = { label: "label", destruction: "destruction", receipt: "receipt", other: "additional" };
export function photoKindLabel(kind: string): string {
  return Object.hasOwn(PHOTO_LABEL, kind) ? PHOTO_LABEL[kind]! : kind;
}

const capitalize = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : s);

/** The reply form's photo buttons: "Label photo", "Receipt photo", and "Additional photo" for any other, the same name
 *  its panel's legend uses. */
export function photoButtonLabel(kind: string): string {
  return `${capitalize(photoKindLabel(kind))} photo`;
}

/** The review step's photo line, in words: "Label and destruction photos added; receipt photo missing". */
export function photoSummary(required: readonly string[], uploaded: readonly string[]): string {
  if (!required.length) return "No photos needed";
  const phrase = (kinds: string[], state: string) => `${listJoin(kinds.map(photoKindLabel))} photo${kinds.length > 1 ? "s" : ""} ${state}`;
  const added = required.filter((k) => uploaded.includes(k));
  const missing = missingPhotos(required, uploaded);
  const parts = [added.length ? phrase(added, "added") : null, missing.length ? phrase(missing, "missing") : null].filter((p): p is string => p !== null);
  return capitalize(parts.join("; "));
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

const DESTRUCTION_GENERIC = "Photograph the product after disabling it as the recall notice instructs.";

/**
 * Whether an extracted destruction instruction reads as words a claimant can follow. The regex extractor (frozen with
 * the ablation) cuts its sentence at any period, so a notice that names a website can leave a URL tail such as
 * "com/3M/en_US/scotch-brand/..., confirm that their laminator is included" (seen on the seeded 3M program), and a
 * quoted word can leave a dangling quote mark ("write \""). Those are never shown as an instruction.
 */
export function instructionReadsAsSentence(text: string): boolean {
  const t = text.trim();
  if (t.split(/\s+/).length < 2 || !/^[A-Za-z]/.test(t)) return false;
  if (/[/\\]|https?:|www\.|\b[\w-]+\.(?:com|net|org|gov|us|co)\b/i.test(t)) return false;
  if (/^(?:com|net|org|gov|us|co|html?)\b/i.test(t)) return false;
  const straight = (t.match(/"/g) ?? []).length;
  const opening = (t.match(/[“]/g) ?? []).length;
  const closing = (t.match(/[”]/g) ?? []).length;
  return straight % 2 === 0 && opening === closing;
}

export function photoHint(kind: string, program: Pick<PortalView["program"], "model_label_location" | "destruction_instruction">): string {
  if (kind === "label") {
    return program.model_label_location
      ? `Photograph the label with the model number. It is ${program.model_label_location}.`
      : "Photograph the label that shows the model number.";
  }
  if (kind === "destruction") {
    const instruction = (program.destruction_instruction ?? "").trim().replace(/[\s.;:,]+$/, "");
    if (!instruction || !instructionReadsAsSentence(instruction)) return DESTRUCTION_GENERIC;
    // A short imperative ("cut the power cord") is the step itself; a whole notice sentence ("Consumers should ...")
    // is quoted as the notice's words rather than turned into a command.
    return /^[a-z]/.test(instruction)
      ? `First do this: ${instruction}. Then photograph the product so the result is visible.`
      : `${DESTRUCTION_GENERIC} The notice says: “${instruction}.”`;
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
      return "If your claim is approved, the company repairs your product for free.";
    case "replacement":
      return "If your claim is approved, the company replaces your product for free.";
    default:
      return "If your claim is approved, the company tells you which remedy applies to you.";
  }
}

/** How an approved refund reaches the claimant: always their choice, on every plan (plan v3). */
export const REFUND_CHOICE = "After approval you choose how to receive it: a bank transfer or a mailed check.";

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
      return { title: "Your claim was not approved", body: "The reasons are below. If you think this is wrong, contact the company running this recall.", tone: "attention" };
    case "paid":
      return { title: "Your refund was sent", body: "Your claim is complete.", tone: "good" };
    case "closed":
      return { title: "Your remedy was delivered", body: "Your claim is complete.", tone: "good" };
    default:
      return { title: "Your claim is in progress", body: "We will update this page as it moves forward.", tone: "neutral" };
  }
}

export const GENERIC_QUESTION = "Please add any information that helps us confirm your claim.";

/**
 * A question from the firm as the claimant reads it. Rules-only runs used to join internal reason codes into the
 * question ("please provide: retailer not_listed; proof of purchase needed"); the backend now words them for
 * claimants, and any text that still carries a snake_case code reads as the generic question instead.
 */
export function claimantQuestion(text: string | null | undefined): string {
  const t = (text ?? "").trim();
  return !t || /[a-z]+_[a-z]+/.test(t) ? GENERIC_QUESTION : t;
}

/** A message time in the reader's own locale and time zone ("Oct 21, 2026, 8:55 PM"); claimants never read UTC. */
export function localDateTime(value: string, opts: { locale?: string; timeZone?: string } = {}): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(opts.locale, { dateStyle: "medium", timeStyle: "short", timeZone: opts.timeZone }).format(d);
}

function approvedNextStep(remedyType: string | null | undefined, payoutStatus: string | null | undefined): string {
  if (payoutStatus) return "Choose how you want to receive your refund, or check its progress below.";
  if (remedyType === "repair") return "The company will contact you to arrange your free repair.";
  if (remedyType === "replacement") return "The company will contact you to arrange your free replacement.";
  return "The company will contact you about your remedy.";
}

/** Who wrote a message in the claim's thread, as the claimant reads it. */
export function senderName(sender: string, companyName: string | null | undefined): string {
  if (sender === "claimant") return "You";
  return companyName?.trim() || "The company";
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

const LONG_DATE = new Intl.DateTimeFormat("en-US", { dateStyle: "long", timeZone: "UTC" });

/** A deadline for people ("November 4, 2026"): one fixed en-US form, the same in the server HTML and the browser. A
 *  YYYY-MM-DD date is read as that calendar day, never shifted by a time zone. */
export function longDate(value: string): string {
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
  return Number.isNaN(d.getTime()) ? value : LONG_DATE.format(d);
}

/**
 * The day a bank-transfer refund still waiting on the claimant becomes a mailed check (PortalView.payout_choice_due,
 * set only while the refund is pending or in Stripe setup). Null when there is no deadline to show.
 */
export function payoutDueSentence(status: string | null | undefined, due: string | null | undefined): string | null {
  if (!due) return null;
  const when = longDate(due);
  if (status === "onboarding") return `Finish your bank setup by ${when}. After that we mail a check to your address.`;
  return `Choose by ${when}. After that we mail a check to your address.`;
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

/** The claim a Stripe return or refresh belongs to: only the one this tab sent to Stripe, read once. Stripe coming back
 *  in another tab finds none, and the claimant opens their private link; no claim this device remembers stands in,
 *  because on a shared device it could be someone else's (review F2). */
export function takeConnectClaim(store: DraftStore | null): string | null {
  try {
    const t = store?.getItem(RETURN_KEY) ?? null;
    store?.removeItem(RETURN_KEY);
    return t && isPlausibleToken(t) ? t : null;
  } catch {
    return null;
  }
}

// ---- What this device remembers (plan v3). A claim's private link is the only way back to it, so each product link
// remembers the claim this device started there, for a short time. Nothing else is kept across tabs: on a shared
// device the next person must never reach the previous claimant's claim (review F2). Best effort: with site data
// blocked or cleared the claimant uses the link they saved, and every page works without it.

/** localStorage as this module uses it: read and write one key, or list them all to forget a claim. */
export type DeviceStore = DraftStore & Pick<Storage, "length" | "key">;

/** This browser's localStorage, or null when site data is blocked (reading the property itself can throw). */
export function localStore(): DeviceStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** A product link remembers the claim it started for 48 hours: long enough to come back and finish it (unsubmitted
 *  drafts are deleted after 48 hours anyway), short enough that a shared device soon stops offering it. */
export const DEVICE_MEMORY_MS = 48 * 60 * 60 * 1000;

const DEVICE_PREFIX = "recallops.";
const startedKey = (intakeToken: string) => `recallops.intake.${intakeToken}`;

type Remembered = { claim_token: string; at: number };

function readRemembered(store: DraftStore | null, key: string, now: number): string | null {
  try {
    const raw = store?.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Remembered> | null;
    const token = parsed?.claim_token;
    const at = parsed?.at;
    if (typeof token !== "string" || !isPlausibleToken(token) || typeof at !== "number" || !Number.isFinite(at)) return null;
    return now - at <= DEVICE_MEMORY_MS ? token : null;
  } catch {
    return null;
  }
}

function writeRemembered(store: DraftStore | null, key: string, claimToken: string, now: number): void {
  if (!isPlausibleToken(claimToken)) return;
  try {
    store?.setItem(key, JSON.stringify({ claim_token: claimToken, at: now } satisfies Remembered));
  } catch {
    // private mode or a full store: the claimant keeps their link instead
  }
}

/** Remembers the claim this device started from one product link, replacing an earlier one from the same link. */
export function rememberStartedClaim(store: DraftStore | null, intakeToken: string, claimToken: string, now = Date.now()): void {
  writeRemembered(store, startedKey(intakeToken), claimToken, now);
}

/** The claim this device started from this product link, if it is recent enough to still open. */
export function startedClaim(store: DraftStore | null, intakeToken: string, now = Date.now()): string | null {
  return readRemembered(store, startedKey(intakeToken), now);
}

function rememberedToken(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const token = (JSON.parse(raw) as Partial<Remembered> | null)?.claim_token;
    return typeof token === "string" ? token : null;
  } catch {
    return null;
  }
}

/**
 * "Forget this claim on this device": removes every reference this browser keeps to one claim, so a shared device
 * stops offering it. That is each product link's memory of it (whatever its age), this tab's Stripe return marker, and
 * this tab's unsent form fields when the claim id is known. False when the browser refused, so the page can say so.
 */
export function forgetClaim(local: DeviceStore | null, session: DraftStore | null, claimToken: string, claimId: string | null = null): boolean {
  let ok = true;
  try {
    if (local) {
      const keys: string[] = [];
      for (let i = 0; i < local.length; i += 1) {
        const key = local.key(i);
        if (key?.startsWith(DEVICE_PREFIX)) keys.push(key);
      }
      // Removed after the scan, because removing shifts the indexes key() reads.
      for (const key of keys) if (rememberedToken(local.getItem(key)) === claimToken) local.removeItem(key);
    }
  } catch {
    ok = false;
  }
  try {
    if (session?.getItem(RETURN_KEY) === claimToken) session.removeItem(RETURN_KEY);
    if (claimId) session?.removeItem(draftKey(claimId));
  } catch {
    ok = false;
  }
  return ok;
}

export const FORGET_LABEL = "Forget this claim on this device";
export const FORGOT_TEXT = "This device no longer remembers this claim. Your private link still opens it.";
export const FORGET_FAILED_TEXT = "This browser did not let the page clear what it stored. Clear this site's data in your browser settings instead.";

// ---- Starting a claim from a product link

/** "1 minute", "12 minutes", "2 hours": a Retry-After in seconds, rounded up, in words. */
export function waitWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "a few minutes";
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) return minutes === 1 ? "1 minute" : `${minutes} minutes`;
  const hours = Math.ceil(minutes / 60);
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

/** A 429 from starting a claim: the API limits new claims per network and per product link each hour. */
export function startLimitSentence(retryAfterSeconds: number | null | undefined): string {
  return `Too many claims were started from this link or this network in a short time. Try again in ${waitWords(retryAfterSeconds)}.`;
}

/** A failed start in words (a 404 is not here: the page shows the link as not active instead). */
export function startSentence(problem: Pick<ApiProblem, "status" | "retryAfterSeconds">): string {
  return problem.status === 429 ? startLimitSentence(problem.retryAfterSeconds) : portalSentence(problem.status);
}

/** Polling budget for the status page: 60 portal requests per link per hour (guards.py), so at most 8 refreshes. */
export const STATUS_REFRESH_MS = 15_000;
export const STATUS_REFRESH_MAX = 8;

export function shouldAutoRefresh(status: string, refreshesSoFar: number): boolean {
  return status === "submitted" && refreshesSoFar < STATUS_REFRESH_MAX;
}

// ---- Replying to a question (needs_info). The API lists the fields and photos the reply form shows, from the checks
// the latest run could not pass. Corrected details or a new photo are checked again; a note alone goes to a person,
// because the AI never reads claimant text (a note would otherwise loop the same question; first live claim).

export const ANSWER_FIELD_LABEL: Record<PortalAnswerField["name"], string> = {
  model_claimed: "Model number",
  serial_claimed: "Serial number",
  date_code_claimed: "Date code",
  retailer_claimed: "Where you bought it",
  purchase_date: "Purchase date",
};

/** The reply body: the note, plus only the details the claimant changed. */
export function answerBody(note: string, edits: Readonly<Record<string, string>>, fields: readonly PortalAnswerField[]): PortalAnswer {
  const body: Record<string, string> = { message: note.trim() };
  for (const f of fields) {
    const value = (edits[f.name] ?? f.value ?? "").trim();
    if (value && value !== (f.value ?? "")) body[f.name] = value;
  }
  return body as PortalAnswer;
}

export type AnswerOutcome = "rechecked" | "to_person" | "empty";

/** What sending will do, said on the form before the claimant sends. */
export function answerOutcome(body: PortalAnswer, photoAdded: boolean): AnswerOutcome {
  const changed = Object.keys(body).some((k) => k !== "message");
  if (changed || photoAdded) return "rechecked";
  return body.message ? "to_person" : "empty";
}

export const ANSWER_OUTCOME_TEXT: Record<AnswerOutcome, string> = {
  rechecked: "We will check your claim again with what you changed.",
  to_person: "A person at the company will read your note. Correct a detail or add a photo to have your claim checked again.",
  empty: "Correct a detail, add the photo we asked for, or write a note.",
};

/** Photo buttons on the reply form: what the question asks for, else the program's required photos; plus another. */
export function answerPhotoKinds(view: Pick<PortalView, "answer_photos" | "program">): string[] {
  const asked = view.answer_photos?.length ? view.answer_photos : view.program.required_photos;
  return [...new Set([...asked, "other"])];
}
