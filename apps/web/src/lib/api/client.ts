// Browser-side JSON calls through the same-origin /api proxy (src/app/api/[...path]/route.ts). No settings and no
// identity here: the proxy attaches both on the server.
import type { z } from "zod";

import { ApiError, problemFrom } from "./problem";

export async function fetchJson<S extends z.ZodType>(schema: S, path: string, search = "", signal?: AbortSignal): Promise<z.infer<S>> {
  let res: Response;
  try {
    res = await fetch(`/api${path}${search}`, { headers: { accept: "application/json" }, cache: "no-store", signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError({ status: 0, title: "Network Error", detail: null, correlationId: null, retryAfterSeconds: null });
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw new ApiError(problemFrom(res.status, body, res.headers));
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new ApiError({
      status: 502,
      title: "Contract Mismatch",
      detail: `the API answer for ${path} does not match the contract`,
      correlationId: res.headers.get("x-request-id"),
      retryAfterSeconds: null,
    });
  }
  return parsed.data;
}
