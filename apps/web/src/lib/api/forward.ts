// Rules for the /api/[...path] proxy (red-team A1, B1, B2). Pure functions so the security rules are unit-tested.
//
// - Only /v1/* (console, firm identity attached server-side) and /portal/* (claim token in the path, no identity)
//   are forwarded. /internal/*, /dev/*, /docs and everything else are refused.
// - Request headers are an allowlist. X-Dev-*, Authorization, Cookie, X-Forwarded-* and Host from the browser
//   never reach the API, so a client cannot choose its firm or role.
// - Response headers are an allowlist too (no Set-Cookie; length and encoding are recomputed by the runtime).

export type Surface = "console" | "portal";

export type Target = {
  surface: Surface;
  /** Upstream path with every segment re-encoded, for example /v1/claims/abc/trace. */
  path: string;
};

export const ALLOWED_METHODS = ["GET", "POST", "PATCH"] as const;
export type AllowedMethod = (typeof ALLOWED_METHODS)[number];

/** Largest request body forwarded: photos are at most 8 MB and registration CSVs 5 MB at the API. */
export const MAX_BODY_BYTES = 10_000_000;

const REQUEST_HEADERS = ["accept", "content-type"] as const;
const RESPONSE_HEADERS = ["content-type", "content-disposition", "retry-after", "cache-control", "etag", "x-request-id"] as const;

// Printable characters only; no separators that could change the upstream path once re-encoded.
const SAFE_SEGMENT = /^[^\u0000-\u001f\u007f/\\]+$/;

export function resolveTarget(segments: readonly string[]): Target | null {
  if (segments.length < 2) return null;
  const [head] = segments;
  const surface: Surface | null = head === "v1" ? "console" : head === "portal" ? "portal" : null;
  if (surface === null) return null;
  for (const s of segments) {
    if (s === "" || s === "." || s === ".." || !SAFE_SEGMENT.test(s)) return null;
  }
  return { surface, path: "/" + segments.map(encodeURIComponent).join("/") };
}

export function isAllowedMethod(method: string): method is AllowedMethod {
  return (ALLOWED_METHODS as readonly string[]).includes(method);
}

export function forwardRequestHeaders(
  incoming: Headers,
  identity: Record<string, string> | null,
  requestId: string,
): Headers {
  const out = new Headers();
  for (const name of REQUEST_HEADERS) {
    const v = incoming.get(name);
    if (v !== null) out.set(name, v);
  }
  if (identity) for (const [k, v] of Object.entries(identity)) out.set(k, v);
  out.set("x-request-id", requestId);
  return out;
}

export function forwardResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const v = upstream.get(name);
    if (v !== null) out.set(name, v);
  }
  out.set("x-content-type-options", "nosniff");
  if (!out.has("cache-control")) out.set("cache-control", "no-store");
  return out;
}

/** A problem detail produced by the web server itself (the API was never reached or refused the shape). */
export function webProblem(status: number, title: string, detail: string, requestId: string): Response {
  return new Response(JSON.stringify({ type: "about:blank", title, status, detail, correlation_id: requestId }), {
    status,
    headers: { "content-type": "application/problem+json", "cache-control": "no-store", "x-request-id": requestId },
  });
}
