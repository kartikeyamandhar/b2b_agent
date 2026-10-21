// react-query options for the claims queue. Polling pauses while the tab is hidden (query-core skips interval
// fetches unless the document is visible) and refreshes at once when the tab is shown again.
import { ClaimPage, Program } from "@/lib/api-types";
import { fetchJson } from "@/lib/api/client";
import { QUEUE_POLL_MS, claimsApiPath, type QueueParams } from "@/lib/claims";

const POLLING = {
  refetchInterval: QUEUE_POLL_MS,
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: true,
  // A failed poll is retried by the next tick, not by a burst of retries.
  retry: false,
  staleTime: 5_000,
} as const;

export function claimsQueueQuery(programId: string, params: QueueParams) {
  const { path, search } = claimsApiPath(programId, params);
  return {
    queryKey: ["claims-queue", programId, params.status, params.cursor] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchJson(ClaimPage, path, search, signal),
    ...POLLING,
  };
}

/** The program's claim counts drive the filter chips and the review summary, so they poll with the queue. */
export function programCountsQuery(programId: string) {
  return {
    queryKey: ["program", programId] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchJson(Program, `/v1/programs/${programId}`, "", signal),
    ...POLLING,
  };
}
