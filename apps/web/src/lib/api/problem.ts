// RFC 7807 problem details from the API (docs/onboarding/ui.md, "Errors"), normalized for display.
// The UI shows a human sentence chosen by status, the API's detail as plain text (never parsed, CHANGES.md 4b),
// and the correlation id so a user can quote it.

export type ApiProblem = {
  status: number;
  title: string;
  detail: string | null;
  correlationId: string | null;
  retryAfterSeconds: number | null;
};

export class ApiError extends Error {
  readonly problem: ApiProblem;

  constructor(problem: ApiProblem) {
    super(`${problem.status} ${problem.title}${problem.detail ? `: ${problem.detail}` : ""}`);
    this.name = "ApiError";
    this.problem = problem;
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** FastAPI's own 422 shape, still listed in the contract next to problem details. */
function validationDetail(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null;
  const parts = detail.filter(isRecord).map((e) => {
    const loc = Array.isArray(e.loc) ? e.loc.filter((p) => p !== "body").join(".") : "";
    const msg = typeof e.msg === "string" ? e.msg : "invalid value";
    return loc ? `${loc}: ${msg}` : msg;
  });
  return parts.length ? parts.join("; ") : null;
}

function retryAfter(value: string | null | undefined): number | null {
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function problemFrom(
  status: number,
  body: unknown,
  headers?: { get(name: string): string | null },
): ApiProblem {
  const headerId = headers?.get("x-request-id") ?? null;
  const base: ApiProblem = {
    status,
    title: defaultTitle(status),
    detail: null,
    correlationId: headerId,
    retryAfterSeconds: retryAfter(headers?.get("retry-after")),
  };
  if (!isRecord(body)) return base;
  const detail =
    typeof body.detail === "string" ? body.detail : validationDetail(body.detail);
  return {
    ...base,
    title: typeof body.title === "string" && body.title ? body.title : base.title,
    detail,
    correlationId: typeof body.correlation_id === "string" && body.correlation_id ? body.correlation_id : headerId,
  };
}

function defaultTitle(status: number): string {
  if (status === 0) return "Network Error";
  if (status === 504) return "Gateway Timeout";
  if (status === 502) return "Bad Gateway";
  return status >= 500 ? "Server Error" : "Request Error";
}

/** One plain sentence per status (payments handoff, "Errors": what the user should do next). */
export function humanSentence(status: number): string {
  switch (status) {
    case 0:
    case 502:
    case 504:
      return "The RecallOps service could not be reached. Check your connection and try again.";
    case 400:
    case 422:
      return "Some of the information needs correcting before it can be saved.";
    case 401:
      return "Your session has ended. Sign in again to continue.";
    case 402:
      return "Your plan does not include this. Upgrade it under Billing.";
    case 403:
      return "This account cannot do this.";
    case 404:
      return "This item was not found, or you do not have access to it.";
    case 409:
      return "This item changed since you opened it. Refresh the page and try again.";
    case 413:
      return "The file is too large.";
    case 429:
      return "Too many requests in a short time. Wait a moment and try again.";
    case 503:
      return "This part of RecallOps is temporarily unavailable. Nothing was lost; try again shortly.";
    default:
      return status >= 500
        ? "Something went wrong on our side. Nothing was saved."
        : "The request could not be completed.";
  }
}

/** A 403 from company verification (the company is not approved for a recall yet), not an account problem. The API
 *  still says "organization" in some details, so both words match. */
export function isAccessPending(problem: Pick<ApiProblem, "status" | "detail">): boolean {
  return problem.status === 403 && /not approved|request access/i.test(problem.detail ?? "");
}

export const ACCESS_PENDING_SENTENCE = "Your company is not approved to run this recall yet.";

/** A 409 because another company holds the recall's approval (one company per recall), which no refresh can fix: the
 *  operator revokes that approval first. */
export function isHeldElsewhere(problem: Pick<ApiProblem, "status" | "detail">): boolean {
  return problem.status === 409 && /another (organization|company) (runs|is already approved for) this recall/i.test(problem.detail ?? "");
}

export const HELD_ELSEWHERE_SENTENCE = "Another company runs this recall. Revoke its approval first if this company should run it instead.";

/** A 409 from POST /v1/access before the company named itself and picked a plan (plan v3 onboarding). */
export function isNotOnboarded(problem: Pick<ApiProblem, "status" | "detail">): boolean {
  return problem.status === 409 && /finish setting up/i.test(problem.detail ?? "");
}

/** The 503 GET /v1/me answers while the API cannot check who is signed in yet (Clerk unreachable). A missing website
 *  setting is also a 503, but a different problem: it keeps its own message. */
export function isSignInUnavailable(problem: Pick<ApiProblem, "status" | "title">): boolean {
  return problem.status === 503 && problem.title !== "Not Configured";
}

export const SIGN_IN_UNAVAILABLE_SENTENCE = "Sign-in verification is unavailable. Try again in a moment.";
