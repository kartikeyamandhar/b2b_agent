// Browser-side JSON calls through the same-origin /api proxy (src/app/api/[...path]/route.ts). No settings and no
// identity here: the proxy attaches both on the server.
import type { z } from "zod";

import { ApiError, problemFrom } from "./problem";

async function call(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, { cache: "no-store", ...init });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError({ status: 0, title: "Network Error", detail: null, correlationId: null, retryAfterSeconds: null });
  }
}

export async function fetchJson<S extends z.ZodType>(schema: S, path: string, search = "", signal?: AbortSignal): Promise<z.infer<S>> {
  const res = await call(`/api${path}${search}`, { headers: { accept: "application/json" }, signal });
  return read(schema, path, res);
}

/** POST or PATCH a JSON body (validated by the caller against the request schema) and parse the answer. */
export async function sendJson<S extends z.ZodType>(schema: S, method: "POST" | "PATCH", path: string, body: unknown): Promise<z.infer<S>> {
  const res = await call(`/api${path}`, {
    method,
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return read(schema, path, res);
}

/** POST multipart form data (photo uploads); the browser sets the boundary. */
export async function sendForm<S extends z.ZodType>(schema: S, path: string, form: FormData, signal?: AbortSignal): Promise<z.infer<S>> {
  const res = await call(`/api${path}`, { method: "POST", headers: { accept: "application/json" }, body: form, signal });
  return read(schema, path, res);
}

async function read<S extends z.ZodType>(schema: S, path: string, res: Response): Promise<z.infer<S>> {
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
