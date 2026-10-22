// Company verification (backend migration 0008; decisions log 2026-10-22): a company runs a recall only after the
// platform operator approves it. Plan v3: approving creates and opens the company's product at once, and revoking
// closes it. Pure rules for the access page and the operator window, so they are unit-tested.
import { OperatorAccessRequest, type AccessRequest, type AccessRequestCreate, type MeFirm, type ProductOutcome, type ProgramCreate } from "@/lib/api-types";
import { z } from "zod";

/** The list the operator window polls from the browser (GET /v1/operator/access). Kept here, not in lib/api, so the
 *  client component never pulls in the server-only loaders. */
export const OperatorAccessList = z.array(OperatorAccessRequest);

/** How often the operator window reads the list again while it is open. */
export const OPERATOR_POLL_MS = 10_000;

const STATUS_LABEL: Record<string, string> = {
  requested: "Waiting for review",
  approved: "Approved",
  rejected: "Not approved",
  revoked: "Access ended",
};

const STATUS_TONE: Record<string, "neutral" | "good" | "attention"> = {
  requested: "neutral",
  approved: "good",
  rejected: "attention",
  revoked: "attention",
};

/** Status words for a request; an unknown (newer) status reads as still under review. */
export function accessStatusLabel(status: string): string {
  return STATUS_LABEL[status] ?? "Under review";
}

export function accessStatusTone(status: string): "neutral" | "good" | "attention" {
  return STATUS_TONE[status] ?? "neutral";
}

const TONE_CLASS = {
  neutral: "border-line bg-panel text-ink",
  good: "border-success/40 bg-success-soft text-success-ink",
  attention: "border-warn/40 bg-warn-soft text-warn-ink",
} as const;

/** The status pill's classes, shared by the access page and the operator window so a status looks the same on both.
 *  It never shrinks or wraps, so it keeps its place beside a long recall title. */
export function accessBadgeClass(status: string): string {
  return `inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASS[accessStatusTone(status)]}`;
}

/** Who asked, in words: requests that migration 0008 made for programs that already existed say so instead of
 *  showing the system actor; a person's id is shown as the API gives it. */
export function requesterLabel(requestedBy: string | null | undefined): string {
  if (!requestedBy) return "Not recorded";
  if (requestedBy === "migration") return "Created automatically for an existing program";
  return requestedBy;
}

export type TypedDraft = { title: string; company: string; notice: string };

/** The request body for a CPSC recall, or for a typed notice; null while the typed form is incomplete. */
export function accessBody(choice: { recallId: string } | TypedDraft): AccessRequestCreate | null {
  if ("recallId" in choice) return { recall_id: choice.recallId };
  const title = choice.title.trim();
  const company = choice.company.trim();
  const notice = choice.notice.trim();
  if (!title || !company || !notice) return null;
  return { typed: { title: title.slice(0, 300), company: company.slice(0, 200), notice: notice.slice(0, 20000) } };
}

/** A recall this company already asked for (or holds), so the search shows its status instead of a button. */
export function requestFor(requests: readonly AccessRequest[], recallId: string): AccessRequest | undefined {
  return requests.find((r) => r.recall_id === recallId && (r.status === "requested" || r.status === "approved"));
}

export type ProductState =
  | { kind: "open"; href: string }
  | { kind: "draft"; href: string }
  | { kind: "closed"; href: string }
  /** Approved, but the approval could not make the product (for example a full plan at the time): make it now. */
  | { kind: "create" }
  | { kind: "none" };

/** What the access page offers for one request's product. */
export function productState(r: Pick<AccessRequest, "status" | "program_id" | "program_status">): ProductState {
  if (r.program_id) {
    const href = `/app/programs/${r.program_id}`;
    if (r.program_status === "open") return { kind: "open", href };
    if (r.program_status === "closed") return { kind: "closed", href };
    return { kind: "draft", href };
  }
  return r.status === "approved" ? { kind: "create" } : { kind: "none" };
}

/** POST /v1/programs for an approved request: the CPSC recall, or the typed notice with its approval. */
export function programBody(r: Pick<AccessRequest, "id" | "recall_id" | "typed" | "title" | "typed_notice">): ProgramCreate {
  if (!r.typed && r.recall_id) return { recall_id: r.recall_id };
  return { manual: { title: r.title, description: r.typed_notice ?? r.title }, access_id: r.id };
}

/** True when a Free company already uses its products (active ones plus requests still waiting), so a new request
 *  would answer 402: the page shows the upgrade prompt before the search instead. */
export function planIsFull(firm: Pick<MeFirm, "product_limit" | "active_products"> | null | undefined, requests: readonly AccessRequest[]): boolean {
  if (!firm || firm.product_limit === null) return false;
  const waiting = requests.filter((r) => r.status === "requested").length;
  return firm.active_products + waiting >= firm.product_limit;
}

/** The operator's choices for a request in its current status. */
export function operatorActions(r: Pick<OperatorAccessRequest, "status">): ("approved" | "rejected" | "revoked")[] {
  if (r.status === "requested") return ["approved", "rejected"];
  if (r.status === "approved") return ["revoked"];
  return [];
}

export const OPERATOR_ACTION_LABEL = { approved: "Approve", rejected: "Reject", revoked: "Revoke" } as const;

export const REVOKE_CONFIRM = "Revoking pauses this company's product: its claim link and its claimants' links stop working until you approve it again.";

/** Newest first: the operator reads the latest request at the top. */
export function newestFirst<T extends Pick<OperatorAccessRequest, "created_at">>(requests: readonly T[]): T[] {
  return [...requests].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** The recall in words: its CPSC number and title, or the typed notice's title. */
export function recallLabel(r: Pick<OperatorAccessRequest, "typed" | "recall_number" | "title">): string {
  if (r.typed) return `Typed notice: ${r.title}`;
  return r.recall_number ? `Recall ${r.recall_number}: ${r.title}` : r.title;
}

/** Warnings about the requester's email the operator must see before trusting it. */
export function emailWarnings(r: Pick<OperatorAccessRequest, "requester_email" | "requester_email_verified" | "requester_test_address">): string[] {
  const out: string[] = [];
  if (r.requester_test_address) out.push("Test address: it proves nothing about the company.");
  if (r.requester_email_verified === false) out.push("This email is not verified.");
  if (!r.requester_email) out.push("No email was recorded for this request.");
  return out;
}

/** What happened to the company's product after an approval, in plain words (POST /v1/operator/access/{id}). */
export function approvalOutcome(product: Pick<ProductOutcome, "status" | "error"> | null | undefined): string {
  if (product?.status === "open") return "Approved. The product is open and its claim link is ready.";
  if (product?.error === "over_plan") return "Approved. The company's plan has no room for another product, so it stays closed until they upgrade.";
  if (product?.error === "missing_refund_amount") return "Approved. The product waits for the company to set a refund amount.";
  return "Approved. The product could not be opened automatically.";
}

/** The sentence after any decision. */
export function decisionOutcome(decision: "approved" | "rejected" | "revoked", product: Pick<ProductOutcome, "status" | "error"> | null | undefined): string {
  if (decision === "approved") return approvalOutcome(product);
  if (decision === "rejected") return "Rejected. The company sees that its request was not approved.";
  return "Revoked. The company's product is closed and its claim link stopped working.";
}

/** One line the operator reads before deciding: what the requester's email domain says about them. */
export function domainHint(r: Pick<OperatorAccessRequest, "requester_domain" | "domain_matches" | "notice_company" | "notice_sites">): string {
  if (!r.requester_domain) return "The requester's email domain is unknown. Check another way, for example by calling the contact number in the notice.";
  const sites = r.notice_sites?.length ? ` (the notice names ${r.notice_sites.join(", ")})` : "";
  return r.domain_matches
    ? `The requester's email is at ${r.requester_domain}, which matches the notice${sites}.`
    : `The requester's email is at ${r.requester_domain}, which does not match ${r.notice_company ?? "the company"}${sites}. Check before approving.`;
}
