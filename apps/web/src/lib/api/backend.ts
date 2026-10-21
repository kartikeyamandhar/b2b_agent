// Server-side calls to the API, shared by the /api proxy route and by server components.
// Every boundary has a timeout and a typed error (ApiError), per the coding standard.
import type { z } from "zod";

import { consoleAuthHeaders } from "./auth-seam";
import { apiConfig, type Env } from "./config";
import { forwardRequestHeaders, type Surface } from "./forward";
import { ApiError, problemFrom } from "./problem";

export type UpstreamInit = {
  method: string;
  headers?: Headers;
  body?: BodyInit | null;
  requestId?: string;
};

/** Raw upstream call. Network failures and timeouts become ApiError with status 502 or 504. */
export async function upstream(surface: Surface, path: string, search: string, init: UpstreamInit, env: Env = process.env): Promise<Response> {
  const { baseUrl, timeoutMs } = apiConfig(env);
  const requestId = init.requestId ?? crypto.randomUUID();
  const identity = surface === "console" ? await consoleAuthHeaders(env) : null;
  const headers = forwardRequestHeaders(init.headers ?? new Headers(), identity, requestId);
  const body = init.body ?? null;
  try {
    return await fetch(`${baseUrl}${path}${search}`, {
      method: init.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
      // Required by Node's fetch to stream a request body (photo uploads).
      ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError({
      status: timedOut ? 504 : 502,
      title: timedOut ? "Gateway Timeout" : "Bad Gateway",
      detail: timedOut ? `the API did not answer within ${timeoutMs} ms` : "the API could not be reached",
      correlationId: requestId,
      retryAfterSeconds: null,
    });
  }
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export type JsonCall = {
  surface: Surface;
  path: string;
  search?: string;
  method?: "GET" | "POST" | "PATCH";
  json?: unknown;
};

/** Typed JSON call for server components: parses the answer with the contract's zod schema. */
export async function apiJson<S extends z.ZodType>(schema: S, call: JsonCall, env: Env = process.env): Promise<z.infer<S>> {
  const headers = new Headers({ accept: "application/json" });
  if (call.json !== undefined) headers.set("content-type", "application/json");
  const res = await upstream(
    call.surface,
    call.path,
    call.search ?? "",
    { method: call.method ?? "GET", headers, body: call.json === undefined ? null : JSON.stringify(call.json) },
    env,
  );
  const body = await readBody(res);
  if (!res.ok) throw new ApiError(problemFrom(res.status, body, res.headers));
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new ApiError({
      status: 502,
      title: "Contract Mismatch",
      detail: `the API answer for ${call.path} does not match the contract: ${parsed.error.issues
        .slice(0, 3)
        .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
        .join("; ")}`,
      correlationId: res.headers.get("x-request-id"),
      retryAfterSeconds: null,
    });
  }
  return parsed.data;
}
