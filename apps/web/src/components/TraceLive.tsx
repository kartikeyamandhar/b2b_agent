"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { Trace } from "@/lib/api-types";
import { ApiError } from "@/lib/api/problem";
import { FIRST_RUN_WATCH_MS, traceWatchPollMs } from "@/lib/claim-detail";
import { traceQuery } from "@/lib/queries/claim";

import { ProblemAlert } from "./ProblemAlert";
import { EmptyState } from "./States";
import { TraceView } from "./TraceView";

/** Polls the trace every 1.5 s while the run is running (trace.md), then stops. A claim with no run yet is watched
 *  every 2 s for up to two minutes after the page loads, so the worker's first run appears without a reload. */
export function TraceLive({ claimId, initialTrace, cited, loadedAt }: { claimId: string; initialTrace: Trace | null; cited: readonly string[]; loadedAt: number }) {
  const client = useQueryClient();
  const [watchUntil] = useState(() => loadedAt + FIRST_RUN_WATCH_MS);
  const extra = () => traceWatchPollMs(client.getQueryData<Trace | null>(["trace", claimId]), watchUntil, Date.now());
  const q = useQuery({ ...traceQuery(claimId, extra), initialData: initialTrace, initialDataUpdatedAt: loadedAt });
  const problem = q.error instanceof ApiError ? q.error.problem : null;
  return (
    <div className="grid gap-3">
      {problem ? <ProblemAlert problem={problem} /> : null}
      {q.data ? (
        <TraceView trace={q.data} claimId={claimId} cited={cited} />
      ) : (
        <EmptyState title="Not adjudicated yet">The claim has no run. The worker picks up submitted claims a few seconds after they arrive.</EmptyState>
      )}
    </div>
  );
}
