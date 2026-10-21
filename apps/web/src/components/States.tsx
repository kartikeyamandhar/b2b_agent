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
    <div className="rounded border border-dashed border-line p-6 text-sm">
      <p className="font-medium text-ink">{title}</p>
      {children ? <div className="mt-1 text-ink-muted">{children}</div> : null}
    </div>
  );
}
