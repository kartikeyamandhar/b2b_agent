import { humanSentence, type ApiProblem } from "@/lib/api/problem";

/** Every API error renders a human sentence plus the correlation id (02-ui.md section 6). */
export function ProblemAlert({ problem, onRetry }: { problem: ApiProblem; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded border border-warn/50 bg-warn-soft p-3 text-sm text-warn-ink">
      <p className="font-medium">{humanSentence(problem.status)}</p>
      {problem.detail ? <p className="mt-1">{problem.detail}</p> : null}
      {problem.retryAfterSeconds !== null ? (
        <p className="mt-1">You can try again in {problem.retryAfterSeconds} seconds.</p>
      ) : null}
      <p className="mt-2 text-xs">
        Error {problem.status}
        {problem.correlationId ? (
          <>
            {" "}
            · reference <code className="font-mono select-all">{problem.correlationId}</code>
          </>
        ) : null}
      </p>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="mt-2 rounded border border-warn/60 px-2 py-1 text-xs font-medium">
          Try again
        </button>
      ) : null}
    </div>
  );
}
