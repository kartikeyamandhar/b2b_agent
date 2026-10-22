import Link from "next/link";

import { queueHref, type ClaimStatus, type QueueParams } from "@/lib/claims";
import { formatCount } from "@/lib/format";
import { AWAITING_REVIEW, programMetrics } from "@/lib/programs";
import { statusLabel } from "@/lib/status";

// Statuses that need a reviewer come first, then the claimant, then the rest of the lifecycle. Claims started from the
// claim link and not submitted (draft) come last: the API leaves them out of the unfiltered queue (plan v3).
const FILTER_ORDER: readonly ClaimStatus[] = [
  "flagged",
  "under_review",
  "capped",
  "needs_info",
  "submitted",
  "approved",
  "denied",
  "paid",
  "closed",
  "draft",
];

const REVIEW: readonly string[] = AWAITING_REVIEW;

function count(counts: Record<string, number> | null, status: string): number | null {
  if (!counts) return null;
  const n = Object.hasOwn(counts, status) ? counts[status] : 0;
  return typeof n === "number" && n > 0 ? n : 0;
}

export function StatusFilter({
  programId,
  active,
  counts,
}: {
  programId: string;
  active: ClaimStatus | null;
  counts: Record<string, number> | null;
}) {
  // "All" is the unfiltered queue, which leaves out claims nobody has submitted yet.
  const total = counts ? Object.entries(counts).reduce((s, [status, n]) => s + (status !== "draft" && n > 0 ? n : 0), 0) : null;
  const chips: Array<{ status: ClaimStatus | null; label: string; n: number | null }> = [
    { status: null, label: "All", n: total },
    ...FILTER_ORDER.map((s) => ({ status: s, label: statusLabel("claim", s).label, n: count(counts, s) })),
  ];
  return (
    <nav aria-label="Filter claims by status">
      <ul className="flex flex-wrap gap-1.5">
        {chips.map((c) => {
          const isActive = c.status === active;
          const review = c.status !== null && REVIEW.includes(c.status);
          return (
            <li key={c.status ?? "all"}>
              <Link
                href={queueHref(programId, { status: c.status, cursor: null })}
                aria-current={isActive ? "page" : undefined}
                data-filter={c.status ?? "all"}
                className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-accent ${
                  isActive
                    ? "border-accent bg-accent text-bg"
                    : review
                      ? "border-warn/60 text-warn-ink hover:bg-warn-soft"
                      : "border-line text-ink hover:bg-panel"
                } hover:opacity-90`}
              >
                {c.label}
                {c.n !== null ? <span className="tabular-nums opacity-80">{formatCount(c.n)}</span> : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function ReviewSummary({ programId, counts }: { programId: string; counts: Record<string, number> | null }) {
  if (!counts) return null;
  const waiting = programMetrics({ claim_counts: counts, units_affected: null }).awaitingReview;
  if (waiting === 0) {
    return <p className="text-sm text-ink-muted" data-review-summary="none">No claims are waiting for a reviewer.</p>;
  }
  return (
    <div className="rounded border border-warn/50 border-l-4 border-l-warn bg-warn-soft px-3 py-2 text-sm text-warn-ink" data-review-summary="waiting">
      <p className="font-medium">
        {formatCount(waiting)} {waiting === 1 ? "claim needs" : "claims need"} a reviewer
      </p>
      <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {AWAITING_REVIEW.map((s) => (
          <li key={s}>
            <Link href={queueHref(programId, { status: s, cursor: null })} className="underline">
              {statusLabel("claim", s).label}: {formatCount(count(counts, s))}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function QueuePager({
  programId,
  params,
  nextCursor,
  shown,
}: {
  programId: string;
  params: QueueParams;
  nextCursor: string | null;
  shown: number;
}) {
  return (
    <nav aria-label="Claims pages" className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <p className="text-xs text-ink-muted">
        {formatCount(shown)} {shown === 1 ? "claim" : "claims"} on this page, oldest first
        {params.cursor ? " (later page)" : ""}
      </p>
      <div className="flex gap-2">
        {params.cursor ? (
          <Link href={queueHref(programId, { status: params.status, cursor: null })} className="rounded border border-line px-2 py-1 text-xs hover:bg-panel" data-page="first">
            First page
          </Link>
        ) : null}
        {nextCursor ? (
          <Link href={queueHref(programId, { status: params.status, cursor: nextCursor })} className="rounded border border-line px-2 py-1 text-xs hover:bg-panel" data-page="next">
            Next page
          </Link>
        ) : null}
      </div>
    </nav>
  );
}
