"use client";

import { useQuery } from "@tanstack/react-query";

import type { Trace } from "@/lib/api-types";
import { ApiError } from "@/lib/api/problem";
import { traceQuery } from "@/lib/queries/claim";

import { ProblemAlert } from "./ProblemAlert";
import { EmptyState } from "./States";
import { TraceView } from "./TraceView";

/** Polls the trace every 1.5 s while the run is running (trace.md), then stops. */
export function TraceLive({ claimId, initialTrace, cited, loadedAt }: { claimId: string; initialTrace: Trace | null; cited: readonly string[]; loadedAt: number }) {
  const q = useQuery({ ...traceQuery(claimId), initialData: initialTrace, initialDataUpdatedAt: loadedAt });
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
