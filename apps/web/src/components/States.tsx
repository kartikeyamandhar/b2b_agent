import type { ReactNode } from "react";

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
