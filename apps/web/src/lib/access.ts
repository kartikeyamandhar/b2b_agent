// Company verification (backend migration 0008; decisions log 2026-10-22): an organization runs a recall only after
// the platform operator approves it. Pure rules for the access page and the operator queue, so they are unit-tested.
import type { AccessRequest, AccessRequestCreate, OperatorAccessRequest } from "@/lib/api-types";

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

/** The status pill's classes, shared by the access page and the operator queue so a status looks the same on both.
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

/** A recall this organization already asked for (or holds), so the search shows its status instead of a button. */
export function requestFor(requests: readonly AccessRequest[], recallId: string): AccessRequest | undefined {
  return requests.find((r) => r.recall_id === recallId && (r.status === "requested" || r.status === "approved"));
}

/** An approved typed notice with no program yet: the access page offers to create it. */
export function typedReadyForProgram(r: AccessRequest): boolean {
  return r.typed && r.status === "approved" && !r.program_id;
}

/** The operator's choices for a request in its current status. */
export function operatorActions(r: Pick<OperatorAccessRequest, "status">): ("approved" | "rejected" | "revoked")[] {
  if (r.status === "requested") return ["approved", "rejected"];
  if (r.status === "approved") return ["revoked"];
  return [];
}

export const OPERATOR_ACTION_LABEL = { approved: "Approve", rejected: "Reject", revoked: "Revoke" } as const;

/** One line the operator reads before deciding: what the requester's email domain says about them. */
export function domainHint(r: Pick<OperatorAccessRequest, "requester_domain" | "domain_matches" | "notice_company" | "notice_sites">): string {
  if (!r.requester_domain) return "The requester's email domain is unknown. Check another way, for example by calling the contact number in the notice.";
  const sites = r.notice_sites?.length ? ` (the notice names ${r.notice_sites.join(", ")})` : "";
  return r.domain_matches
    ? `The requester's email is at ${r.requester_domain}, which matches the notice${sites}.`
    : `The requester's email is at ${r.requester_domain}, which does not match ${r.notice_company ?? "the company"}${sites}. Check before approving.`;
}
