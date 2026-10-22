// Server-side calls to the API, shared by the /api proxy route and by server components.
// Every boundary has a timeout and a typed error (ApiError), per the coding standard.
import type { z } from "zod";

import { consoleAuthHeaders } from "./auth-seam";
import { apiConfig, type Env } from "./config";
import { clientAddress, forwardRequestHeaders, portalClientHeaders, type Surface } from "./forward";
import { ApiError, problemFrom } from "./problem";

export type UpstreamInit = {
  method: string;
  headers?: Headers;
  body?: BodyInit | null;
  requestId?: string;
  /** A shorter deadline than RECALLOPS_API_TIMEOUT_MS for a call a page must not wait on. */
  timeoutMs?: number;
};

/** The claimant's address for a portal call: the proxied request's headers when they name one, else the page request's.
 *  A server component's own read (apiJson) passes only accept and content-type, so without the fallback every
 *  claimant page would reach the API as this server and share its one rate-limit bucket. */
async function portalClientIp(incoming: Headers | undefined): Promise<string | null> {
  const forwarded = incoming ? clientAddress(incoming) : null;
  if (forwarded) return forwarded;
  try {
    const { headers } = await import("next/headers");
    return clientAddress(new Headers(await headers()));
  } catch {
    return null; // outside a request (tests, scripts): the API keys on this server's address as before
  }
}

/** Raw upstream call. Network failures and timeouts become ApiError with status 502 or 504. */
export async function upstream(surface: Surface, path: string, search: string, init: UpstreamInit, env: Env = process.env): Promise<Response> {
  const config = apiConfig(env);
  const baseUrl = config.baseUrl;
  const timeoutMs = Math.min(config.timeoutMs, init.timeoutMs ?? config.timeoutMs);
  const requestId = init.requestId ?? crypto.randomUUID();
  const identity = surface === "console" ? await consoleAuthHeaders(env) : null;
  const headers = forwardRequestHeaders(init.headers ?? new Headers(), identity, requestId);
  if (surface === "portal") {
    const named = portalClientHeaders(await portalClientIp(init.headers), env.RECALLOPS_WEB_PROXY_SECRET);
    for (const [k, v] of Object.entries(named)) headers.set(k, v);
  }
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

/** The answer's body. The request's deadline also covers the body, and a body that stalls past it or is cut off
 *  (an API restart mid-answer) is an ApiError like any other failed call, never a raw error that crashes a layout. */
async function readBody(res: Response): Promise<unknown> {
  let text: string;
  try {
    text = await res.text();
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError({
      status: timedOut ? 504 : 502,
      title: timedOut ? "Gateway Timeout" : "Bad Gateway",
      detail: timedOut ? "the API did not finish its answer in time" : "the API answer was cut off",
      correlationId: res.headers.get("x-request-id"),
      retryAfterSeconds: null,
    });
  }
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
  timeoutMs?: number;
};

/** Typed JSON call for server components: parses the answer with the contract's zod schema. */
export async function apiJson<S extends z.ZodType>(schema: S, call: JsonCall, env: Env = process.env): Promise<z.infer<S>> {
  const headers = new Headers({ accept: "application/json" });
  if (call.json !== undefined) headers.set("content-type", "application/json");
  const res = await upstream(
    call.surface,
    call.path,
    call.search ?? "",
    { method: call.method ?? "GET", headers, body: call.json === undefined ? null : JSON.stringify(call.json), timeoutMs: call.timeoutMs },
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
