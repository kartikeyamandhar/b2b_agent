// Same-origin proxy to the API for client components (react-query polling, uploads, actions). The API has no
// CORS, and identity must be attached on the server; see src/lib/api/forward.ts for the rules.
import { NotSignedIn } from "@/lib/api/auth-seam";
import { upstream } from "@/lib/api/backend";
import { ConfigError } from "@/lib/api/config";
import {
  MAX_BODY_BYTES,
  forwardResponseHeaders,
  isAllowedMethod,
  isSameOriginWrite,
  resolveTarget,
  webProblem,
} from "@/lib/api/forward";
import { ApiError } from "@/lib/api/problem";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context): Promise<Response> {
  const requestId = crypto.randomUUID();
  const { path } = await context.params;
  const target = resolveTarget(path);
  if (target === null) return webProblem(404, "Not Found", "no such API route", requestId);
  if (!isAllowedMethod(request.method)) {
    return webProblem(405, "Method Not Allowed", `${request.method} is not forwarded`, requestId);
  }
  if (request.method !== "GET" && !isSameOriginWrite(request.headers)) {
    return webProblem(403, "Forbidden", "cross-site requests are refused", requestId);
  }
  const length = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) {
    return webProblem(413, "Payload Too Large", "the request body is too large", requestId);
  }
  const hasBody = request.method !== "GET" && request.body !== null;
  try {
    const res = await upstream(target.surface, target.path, new URL(request.url).search, {
      method: request.method,
      headers: request.headers,
      body: hasBody ? request.body : null,
      requestId,
    });
    return new Response(res.body, { status: res.status, headers: forwardResponseHeaders(res.headers) });
  } catch (err) {
    if (err instanceof ApiError) {
      const p = err.problem;
      return webProblem(p.status, p.title, p.detail ?? "", p.correlationId ?? requestId);
    }
    if (err instanceof NotSignedIn) {
      return webProblem(401, "Unauthorized", "sign in to continue", requestId);
    }
    if (err instanceof ConfigError) {
      console.error(JSON.stringify({ level: "error", event: "web_proxy_config_error", request_id: requestId, error: err.message }));
      return webProblem(503, "Service Unavailable", "the web server is not configured to reach the API", requestId);
    }
    console.error(JSON.stringify({ level: "error", event: "web_proxy_error", request_id: requestId }));
    return webProblem(500, "Internal Server Error", "unexpected web server error; nothing was sent", requestId);
  }
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const PUT = handle;
export const DELETE = handle;
