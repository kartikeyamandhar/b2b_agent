import type { ReactNode } from "react";

import { SIGN_IN_UNAVAILABLE_SENTENCE } from "@/lib/api/problem";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="py-6 text-sm text-ink-muted">
      {label}…
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong/60 bg-surface p-8 text-sm">
      <p className="display-title text-xl">{title}</p>
      {children ? <div className="mt-2 text-ink-muted">{children}</div> : null}
    </div>
  );
}

/** GET /v1/me answered 503: the API cannot check who is signed in yet. A short state with a full reload, never a
 *  crash or an empty console. */
export function SignInUnavailable({ retryHref }: { retryHref: string }) {
  return (
    <div role="status" className="grid max-w-md gap-2 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink" data-sign-in-unavailable>
      <p className="font-medium">{SIGN_IN_UNAVAILABLE_SENTENCE}</p>
      <p>
        <a href={retryHref} className="font-medium underline">
          Try again
        </a>
      </p>
    </div>
  );
}
