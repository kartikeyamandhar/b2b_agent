import Link from "next/link";

import type { ClaimSummary } from "@/lib/api-types";
import { REVIEW_LABEL, reviewState, shortId } from "@/lib/claims";
import { formatCents, formatDateTime } from "@/lib/format";

import { StatusBadge } from "./StatusBadge";

const th = "px-3 py-2 text-left text-xs font-medium text-ink-muted";
const muted = <span className="text-ink-muted">—</span>;

/** The queue table (02-ui.md component 2). Claims that need a reviewer carry a marker and a text label. */
export function ClaimsTable({ claims }: { claims: readonly ClaimSummary[] }) {
  return (
    <div className="overflow-x-auto rounded border border-line">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Claims, oldest first</caption>
        <thead className="border-b border-line bg-panel">
          <tr>
            <th scope="col" className={`${th} border-l-4 border-l-transparent`}>Claim</th>
            <th scope="col" className={th}>Status</th>
            <th scope="col" className={th}>Outcome</th>
            <th scope="col" className={th}>Review</th>
            <th scope="col" className={`${th} hidden md:table-cell`}>Model</th>
            <th scope="col" className={`${th} hidden lg:table-cell`}>Retailer</th>
            <th scope="col" className={`${th} hidden md:table-cell text-right`}>Refund</th>
            <th scope="col" className={th}>Created</th>
            <th scope="col" className={`${th} hidden sm:table-cell`}>Decided</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c) => {
            const review = reviewState(c);
            const needsReviewer = review === "needs_reviewer";
            return (
              <tr
                key={c.id}
                data-claim-id={c.id}
                data-review={review}
                className={`border-b border-line last:border-b-0 ${needsReviewer ? "bg-warn-soft" : "hover:bg-panel"}`}
              >
                <th
                  scope="row"
                  className={`border-l-4 px-3 py-2 text-left font-normal ${needsReviewer ? "border-l-warn" : "border-l-transparent"}`}
                >
                  <Link
                    href={`/app/claims/${c.id}`}
                    className="font-mono text-xs text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent"
                    title={c.id}
                  >
                    {shortId(c.id)}
                    <span className="sr-only"> (claim {c.id})</span>
                  </Link>
                </th>
                <td className="px-3 py-2">
                  <StatusBadge kind="claim" value={c.status} />
                </td>
                <td className="px-3 py-2">{c.outcome ? <StatusBadge kind="outcome" value={c.outcome} /> : muted}</td>
                <td className={`px-3 py-2 whitespace-nowrap ${needsReviewer ? "font-medium text-warn-ink" : "text-ink-muted"}`}>
                  {REVIEW_LABEL[review] || muted}
                </td>
                <td className="hidden px-3 py-2 font-mono text-xs md:table-cell">{c.model_claimed ?? muted}</td>
                <td className="hidden max-w-48 truncate px-3 py-2 lg:table-cell" title={c.retailer_claimed ?? undefined}>
                  {c.retailer_claimed ?? muted}
                </td>
                <td className="hidden px-3 py-2 text-right tabular-nums md:table-cell">
                  {c.refund_cents === null ? muted : formatCents(c.refund_cents)}
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-xs">{formatDateTime(c.created_at)}</td>
                <td className="hidden px-3 py-2 whitespace-nowrap text-xs sm:table-cell">{formatDateTime(c.decided_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
