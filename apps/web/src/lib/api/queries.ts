// Console reads for server components. Each returns a result instead of throwing for API and configuration
// errors, so a page renders its error state (ProblemAlert with the correlation id) rather than a blank boundary.
import { CampaignSummary, ClaimDetail, ClaimPage, ExportBatchList, FraudGraphView, MprReport, PortalView, Program, ProgramList, Trace, Usage } from "@/lib/api-types";
import { isPlausibleToken, portalPath } from "@/lib/portal";
import { claimsApiPath, type QueueParams } from "@/lib/claims";
import { EXPORT_LIST_LIMIT } from "@/lib/payouts";

import { NotSignedIn } from "./auth-seam";
import { apiJson } from "./backend";
import { ConfigError, type Env } from "./config";
import { ApiError, type ApiProblem } from "./problem";

export type Loaded<T> = { ok: true; data: T } | { ok: false; problem: ApiProblem };

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function load<T>(run: () => Promise<T>): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await run() };
  } catch (err) {
    if (err instanceof ApiError) return { ok: false, problem: err.problem };
    if (err instanceof NotSignedIn) {
      return { ok: false, problem: { status: 401, title: "Unauthorized", detail: err.message, correlationId: null, retryAfterSeconds: null } };
    }
    if (err instanceof ConfigError) {
      return {
        ok: false,
        problem: { status: 503, title: "Not Configured", detail: err.message, correlationId: null, retryAfterSeconds: null },
      };
    }
    throw err;
  }
}

// A malformed id can never name a program; answer like the API does for another firm's program (404) without
// sending it.
const PROGRAM_NOT_FOUND: Loaded<never> = {
  ok: false,
  problem: { status: 404, title: "Not Found", detail: "program not found", correlationId: null, retryAfterSeconds: null },
};

export function loadPrograms(env: Env = process.env): Promise<Loaded<ProgramList>> {
  return load(() => apiJson(ProgramList, { surface: "console", path: "/v1/programs" }, env));
}

export function loadProgram(id: string, env: Env = process.env): Promise<Loaded<Program>> {
  if (!UUID.test(id)) return Promise.resolve(PROGRAM_NOT_FOUND);
  return load(() => apiJson(Program, { surface: "console", path: `/v1/programs/${id}` }, env));
}

export function loadClaims(programId: string, params: QueueParams, env: Env = process.env): Promise<Loaded<ClaimPage>> {
  if (!UUID.test(programId)) return Promise.resolve(PROGRAM_NOT_FOUND);
  const { path, search } = claimsApiPath(programId, params);
  return load(() => apiJson(ClaimPage, { surface: "console", path, search }, env));
}

const CLAIM_NOT_FOUND: Loaded<never> = {
  ok: false,
  problem: { status: 404, title: "Not Found", detail: "claim not found", correlationId: null, retryAfterSeconds: null },
};

export function loadClaim(id: string, env: Env = process.env): Promise<Loaded<ClaimDetail>> {
  if (!UUID.test(id)) return Promise.resolve(CLAIM_NOT_FOUND);
  return load(() => apiJson(ClaimDetail, { surface: "console", path: `/v1/claims/${id}` }, env));
}

/** The claim's latest run. A 404 for a claim that exists means no run yet (a submitted or draft claim): data null. */
export async function loadTrace(id: string, env: Env = process.env): Promise<Loaded<Trace | null>> {
  if (!UUID.test(id)) return CLAIM_NOT_FOUND;
  const r = await load(() => apiJson(Trace, { surface: "console", path: `/v1/claims/${id}/trace` }, env));
  if (!r.ok && r.problem.status === 404 && r.problem.detail === "no run for this claim") return { ok: true, data: null };
  return r;
}

const LINK_NOT_FOUND: Loaded<never> = {
  ok: false,
  problem: { status: 404, title: "Not Found", detail: null, correlationId: null, retryAfterSeconds: null },
};

/** The claim portal view, read with the claim token only (no console identity). */
export function loadPortal(token: string, env: Env = process.env): Promise<Loaded<PortalView>> {
  if (!isPlausibleToken(token)) return Promise.resolve(LINK_NOT_FOUND);
  return load(() => apiJson(PortalView, { surface: "portal", path: portalPath(token) }, env));
}

export function loadUsage(env: Env = process.env): Promise<Loaded<Usage>> {
  return load(() => apiJson(Usage, { surface: "console", path: "/v1/usage" }, env));
}

/** The latest report for a period; "no report for that period" is data (null), not an error. */
export async function loadMpr(programId: string, period: string, env: Env = process.env): Promise<Loaded<MprReport | null>> {
  if (!UUID.test(programId)) return PROGRAM_NOT_FOUND;
  const r = await load(() => apiJson(MprReport, { surface: "console", path: `/v1/programs/${programId}/mpr`, search: `?period=${encodeURIComponent(period)}` }, env));
  if (!r.ok && r.problem.status === 404 && (r.problem.detail ?? "").startsWith("no report for that period")) return { ok: true, data: null };

  return r;
}

export function loadExports(env: Env = process.env): Promise<Loaded<ExportBatchList>> {
  return load(() => apiJson(ExportBatchList, { surface: "console", path: "/v1/payouts/exports", search: `?limit=${EXPORT_LIST_LIMIT}` }, env));
}

export function loadFraudGraph(programId: string, limit = 500, env: Env = process.env): Promise<Loaded<FraudGraphView>> {
  if (!UUID.test(programId)) return Promise.resolve(PROGRAM_NOT_FOUND);
  return load(() => apiJson(FraudGraphView, { surface: "console", path: `/v1/programs/${programId}/fraud-graph`, search: `?limit=${limit}` }, env));
}

export function loadCampaign(programId: string, env: Env = process.env): Promise<Loaded<CampaignSummary>> {
  if (!UUID.test(programId)) return Promise.resolve(PROGRAM_NOT_FOUND);
  return load(() => apiJson(CampaignSummary, { surface: "console", path: `/v1/programs/${programId}/campaign` }, env));
}
