// react-query options for the claim detail and trace pages.
import { ClaimDetail, Trace } from "@/lib/api-types";
import { fetchJson } from "@/lib/api/client";
import { ApiError } from "@/lib/api/problem";
import { claimPollMs, tracePollMs } from "@/lib/claim-detail";

export function claimDetailQuery(id: string, awaitingWorkerUntil: () => number) {
  return {
    queryKey: ["claim", id] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchJson(ClaimDetail, `/v1/claims/${id}`, "", signal),
    refetchInterval: (q: { state: { data: ClaimDetail | undefined } }) => claimPollMs(q.state.data, awaitingWorkerUntil(), Date.now()),
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 2_000,
  };
}

/** A claim without a run answers 404 "no run for this claim"; that is data (null), not an error. */
export async function fetchTrace(id: string, signal?: AbortSignal): Promise<Trace | null> {
  try {
    return await fetchJson(Trace, `/v1/claims/${id}/trace`, "", signal);
  } catch (err) {
    if (err instanceof ApiError && err.problem.status === 404 && err.problem.detail === "no run for this claim") return null;
    throw err;
  }
}

export function traceQuery(id: string, extraPollMs: () => number | false = () => false) {
  return {
    queryKey: ["trace", id] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchTrace(id, signal),
    refetchInterval: (q: { state: { data: Trace | null | undefined } }) => tracePollMs(q.state.data) || extraPollMs(),
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 1_000,
  };
}
