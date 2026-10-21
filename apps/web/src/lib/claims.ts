// Claims queue: URL state, API paths, and review classification. Pure, so the rules are unit-tested.
import { ClaimSummaryStatusValues, type ClaimSummary } from "@/lib/api-types";

export const QUEUE_PAGE_SIZE = 50;
/** 02-ui.md section 3: poll the queue every 10 seconds while the tab is visible. */
export const QUEUE_POLL_MS = 10_000;

export type ClaimStatus = (typeof ClaimSummaryStatusValues)[number];

export type QueueParams = { status: ClaimStatus | null; cursor: string | null };

export type ParsedQueueParams = QueueParams & {
  /** A ?status= value the contract does not list; the queue shows every claim instead and says so. */
  ignoredStatus: string | null;
  /** A ?cursor= value that cannot be one the API issued; the queue starts from the first page. */
  ignoredCursor: boolean;
};

type SearchParams = Record<string, string | string[] | undefined>;

const first = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

// The API issues URL-safe base64 of "<created_at>|<uuid>"; anything else would only earn a 400.
const CURSOR = /^[A-Za-z0-9_-]+={0,2}$/;
const MAX_CURSOR = 256;

export function isClaimStatus(v: string): v is ClaimStatus {
  return (ClaimSummaryStatusValues as readonly string[]).includes(v);
}

export function parseQueueParams(search: SearchParams): ParsedQueueParams {
  const rawStatus = first(search.status) ?? "";
  const rawCursor = first(search.cursor) ?? "";
  const status = rawStatus && isClaimStatus(rawStatus) ? rawStatus : null;
  const cursorOk = rawCursor.length > 0 && rawCursor.length <= MAX_CURSOR && CURSOR.test(rawCursor);
  return {
    status,
    cursor: cursorOk ? rawCursor : null,
    ignoredStatus: rawStatus && !status ? rawStatus : null,
    ignoredCursor: rawCursor.length > 0 && !cursorOk,
  };
}

function query(p: QueueParams, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams();
  if (p.status) q.set("status", p.status);
  if (p.cursor) q.set("cursor", p.cursor);
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Console URL of a queue view. Changing the filter always starts from the first page. */
export function queueHref(programId: string, p: QueueParams): string {
  return `/app/programs/${programId}/claims${query(p)}`;
}

/** API path and query for GET /v1/programs/{id}/claims. */
export function claimsApiPath(programId: string, p: QueueParams): { path: string; search: string } {
  return { path: `/v1/programs/${programId}/claims`, search: query(p, { limit: String(QUEUE_PAGE_SIZE) }) };
}

export type ReviewState = "needs_reviewer" | "waiting_claimant" | "being_checked" | "routed" | "none";

const NEEDS_REVIEWER: readonly string[] = ["flagged", "under_review", "capped"];

/** What a reviewer should take from a row. Keyed on the claim status, not the run (trace.md, CHANGES.md item 6). */
export function reviewState(c: Pick<ClaimSummary, "status" | "routed_to_human">): ReviewState {
  if (NEEDS_REVIEWER.includes(c.status)) return "needs_reviewer";
  if (c.status === "needs_info") return "waiting_claimant";
  if (c.status === "submitted") return "being_checked";
  if (c.routed_to_human) return "routed";
  return "none";
}

export const REVIEW_LABEL: Record<ReviewState, string> = {
  needs_reviewer: "Needs a reviewer",
  waiting_claimant: "Waiting on claimant",
  being_checked: "Being checked",
  routed: "Decided by a person",
  none: "",
};

export function shortId(id: string): string {
  return id.slice(0, 8);
}
