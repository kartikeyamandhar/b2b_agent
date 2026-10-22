import Link from "next/link";

import { ACCESS_PENDING_SENTENCE, HELD_ELSEWHERE_SENTENCE, humanSentence, isAccessPending, isHeldElsewhere, type ApiProblem } from "@/lib/api/problem";

/** The API's detail as a sentence ("program not found" reads "Program not found."); none for a 404, whose human
 *  sentence already says it. */
export function detailSentence(problem: Pick<ApiProblem, "status" | "detail">): string | null {
  const d = problem.detail?.trim();
  if (!d || problem.status === 404) return null;
  const s = d.charAt(0).toUpperCase() + d.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}

/** Every API error renders a human sentence plus the correlation id (02-ui.md section 6). Claimants (the portal) see
 *  the reference only: a status code means nothing to them, and "Error 0" (a dropped connection) even less. A known
 *  company-verification answer gets its own sentence in place of the API's detail, which still says "organization". */
export function ProblemAlert({
  problem,
  onRetry,
  sentence,
  showDetail = true,
  audience = "console",
}: {
  problem: ApiProblem;
  onRetry?: () => void;
  sentence?: string;
  showDetail?: boolean;
  audience?: "console" | "claimant";
}) {
  const pending = isAccessPending(problem);
  const heldElsewhere = isHeldElsewhere(problem);
  const detail = showDetail && !pending && !heldElsewhere ? detailSentence(problem) : null;
  const code = audience === "console" && problem.status > 0 ? `Error ${problem.status}` : null;
  return (
    <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft p-4 text-sm text-danger-ink">
      <p className="font-medium">
        {sentence ?? (pending ? ACCESS_PENDING_SENTENCE : heldElsewhere ? HELD_ELSEWHERE_SENTENCE : humanSentence(problem.status))}
      </p>
      {detail ? <p className="mt-1">{detail}</p> : null}
      {pending ? (
        <p className="mt-1">
          <Link href="/app/access" className="font-medium underline">
            Go to Recall access
          </Link>
        </p>
      ) : null}
      {problem.retryAfterSeconds !== null ? (
        <p className="mt-1">You can try again in {problem.retryAfterSeconds} seconds.</p>
      ) : null}
      {code || problem.correlationId ? (
        <p className="mt-2 text-xs">
          {code}
          {code && problem.correlationId ? " · " : null}
          {problem.correlationId ? (
            <>
              {code ? "reference" : "Reference"} <code className="font-mono select-all">{problem.correlationId}</code>
            </>
          ) : null}
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" onClick={onRetry} className="mt-3 rounded-md border border-danger/40 bg-surface px-3 py-1.5 text-xs font-medium">
          Try again
        </button>
      ) : null}
    </div>
  );
}
