import { humanSentence, type ApiProblem } from "@/lib/api/problem";

/** Every API error renders a human sentence plus the correlation id (02-ui.md section 6). */
export function ProblemAlert({
  problem,
  onRetry,
  sentence,
  showDetail = true,
}: {
  problem: ApiProblem;
  onRetry?: () => void;
  sentence?: string;
  showDetail?: boolean;
}) {
  return (
    <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft p-4 text-sm text-danger-ink">
      <p className="font-medium">{sentence ?? humanSentence(problem.status)}</p>
      {showDetail && problem.detail ? <p className="mt-1">{problem.detail}</p> : null}
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
        <button type="button" onClick={onRetry} className="mt-3 rounded-md border border-danger/40 bg-surface px-3 py-1.5 text-xs font-medium">
          Try again
        </button>
      ) : null}
    </div>
  );
}
