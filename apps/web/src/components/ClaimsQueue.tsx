"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { ClaimPage, Program } from "@/lib/api-types";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import type { Loaded } from "@/lib/api/queries";
import { QUEUE_POLL_MS, type QueueParams } from "@/lib/claims";
import { formatClock } from "@/lib/format";
import { claimsQueueQuery, programCountsQuery } from "@/lib/queries/claims-queue";
import { moveIndex, queueMove, shortcutKey } from "@/lib/shortcuts";
import { statusLabel } from "@/lib/status";

import { ClaimsTable } from "./ClaimsTable";
import { ProblemAlert } from "./ProblemAlert";
import { QueuePager, ReviewSummary, StatusFilter } from "./QueueControls";
import { EmptyState, LoadingState } from "./States";

function toProblem(err: unknown): ApiProblem {
  if (err instanceof ApiError) return err.problem;
  return { status: 500, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
}

export type ClaimsQueueProps = {
  programId: string;
  params: QueueParams;
  /** The server's first load (rendered without JavaScript too); polling takes over in the browser. */
  initialPage: Loaded<ClaimPage>;
  initialProgram: Program;
  loadedAt: number;
};

export function ClaimsQueue({ programId, params, initialPage, initialProgram, loadedAt }: ClaimsQueueProps) {
  const claims = useQuery({
    ...claimsQueueQuery(programId, params),
    initialData: initialPage.ok ? initialPage.data : undefined,
    initialDataUpdatedAt: loadedAt,
  });
  const program = useQuery({ ...programCountsQuery(programId), initialData: initialProgram, initialDataUpdatedAt: loadedAt });

  const counts = program.data?.claim_counts ?? null;
  const page = claims.data;
  const problem = claims.error ? toProblem(claims.error) : !page && !initialPage.ok ? initialPage.problem : null;

  // j and k move through the visible page, Enter opens the selected claim (02-ui.md section 6). The selection is kept
  // by claim id, so a poll that reorders or adds claims never moves it to another claim.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const ids = page?.items.map((c) => c.id) ?? [];
  const idsKey = ids.join(",");
  useEffect(() => {
    const list = idsKey ? idsKey.split(",") : [];
    const visibleRow = (id: string) =>
      [...document.querySelectorAll<HTMLElement>(`[data-claim-id="${id}"], [data-card-claim-id="${id}"]`)].find((n) => n.offsetParent !== null);
    function onKey(e: KeyboardEvent) {
      const move = queueMove(shortcutKey(e));
      if (!move) return;
      if (move === "open") {
        // Enter on a focused link or button keeps its own meaning.
        const t = e.target as HTMLElement | null;
        if (!selectedId || (t && (t.tagName === "A" || t.tagName === "BUTTON"))) return;
        e.preventDefault();
        // The row's own link navigates, so Enter behaves exactly like a click on the claim.
        visibleRow(selectedId)?.querySelector<HTMLAnchorElement>("a[href]")?.click();
        return;
      }
      e.preventDefault();
      const at = selectedId ? list.indexOf(selectedId) : -1;
      const next = moveIndex(at < 0 ? null : at, move, list.length);
      const id = next === null ? null : list[next]!;
      setSelectedId(id);
      if (id) visibleRow(id)?.scrollIntoView({ block: "nearest" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idsKey, selectedId]);

  return (
    <div className="grid gap-3">
      <ReviewSummary programId={programId} counts={counts} />
      <StatusFilter programId={programId} active={params.status} counts={counts} />

      <p className="text-xs text-ink-muted" data-refresh>
        {page ? `Updated ${formatClock(claims.dataUpdatedAt)}` : "Not loaded yet"} · refreshes every {QUEUE_POLL_MS / 1000} s
        while this tab is visible{claims.isFetching ? " · refreshing…" : ""}
      </p>

      {problem && page ? (
        // A failed poll keeps the last good page on screen; the next tick tries again.
        <div data-stale>
          <p className="mb-1 text-xs text-warn-ink">Could not refresh; showing the last loaded claims.</p>
          <ProblemAlert problem={problem} />
        </div>
      ) : null}

      {page ? (
        page.items.length === 0 ? (
          <EmptyState title={params.status ? `No claims with status ${statusLabel("claim", params.status).label}` : "No claims yet"}>
            {params.cursor
              ? "There are no more claims after the previous page."
              : "Claims appear here as claimants submit them through the portal."}
          </EmptyState>
        ) : (
          <>
            <ClaimsTable claims={page.items} selectedId={selectedId} />
            <p className="hidden text-xs text-ink-muted md:block" data-shortcut-hint>
              Keyboard: <kbd className="font-mono">j</kbd> next · <kbd className="font-mono">k</kbd> previous · <kbd className="font-mono">Enter</kbd> open the selected claim.
            </p>
            <QueuePager programId={programId} params={params} nextCursor={page.next_cursor} shown={page.items.length} />
          </>
        )
      ) : problem ? (
        <ProblemAlert problem={problem} onRetry={() => void claims.refetch()} />
      ) : (
        <LoadingState label="Loading claims" />
      )}
    </div>
  );
}
