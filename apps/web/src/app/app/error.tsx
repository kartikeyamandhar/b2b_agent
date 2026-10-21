"use client";

// Last-resort boundary for unexpected errors in the console. API errors never reach it: pages render them with
// ProblemAlert. Production hides the message, so only the digest is shown as a reference.
export default function ConsoleError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="rounded border border-warn/50 bg-warn-soft p-3 text-sm text-warn-ink">
      <p className="font-medium">Something went wrong while showing this page. Nothing was saved.</p>
      {error.digest ? (
        <p className="mt-2 text-xs">
          Reference <code className="font-mono select-all">{error.digest}</code>
        </p>
      ) : null}
      <button type="button" onClick={reset} className="mt-2 rounded border border-warn/60 px-2 py-1 text-xs font-medium">
        Try again
      </button>
    </div>
  );
}
