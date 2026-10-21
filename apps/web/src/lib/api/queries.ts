// Console reads for server components. Each returns a result instead of throwing for API and configuration
// errors, so a page renders its error state (ProblemAlert with the correlation id) rather than a blank boundary.
import { Program, ProgramList } from "@/lib/api-types";

import { apiJson } from "./backend";
import { ConfigError, type Env } from "./config";
import { ApiError, type ApiProblem } from "./problem";

export type Loaded<T> = { ok: true; data: T } | { ok: false; problem: ApiProblem };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function load<T>(run: () => Promise<T>): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await run() };
  } catch (err) {
    if (err instanceof ApiError) return { ok: false, problem: err.problem };
    if (err instanceof ConfigError) {
      return {
        ok: false,
        problem: { status: 503, title: "Not Configured", detail: err.message, correlationId: null, retryAfterSeconds: null },
      };
    }
    throw err;
  }
}

export function loadPrograms(env: Env = process.env): Promise<Loaded<ProgramList>> {
  return load(() => apiJson(ProgramList, { surface: "console", path: "/v1/programs" }, env));
}

export function loadProgram(id: string, env: Env = process.env): Promise<Loaded<Program>> {
  // A malformed id can never name a program; answer like the API does for another firm's program (404).
  if (!UUID.test(id)) {
    return Promise.resolve({
      ok: false,
      problem: { status: 404, title: "Not Found", detail: "program not found", correlationId: null, retryAfterSeconds: null },
    });
  }
  return load(() => apiJson(Program, { surface: "console", path: `/v1/programs/${id}` }, env));
}
