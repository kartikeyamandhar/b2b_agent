import { statusLabel, type StatusKind } from "@/lib/status";

// Semantic colors only: green done, amber needs attention, red negative; neutral and in-progress stay ink.
const TONE_CLASS = {
  neutral: "border-line bg-surface text-ink-muted",
  progress: "border-line-strong bg-surface text-ink",
  done: "border-success/30 bg-success-soft text-success-ink",
  attention: "border-warn/40 bg-warn-soft text-warn-ink",
  negative: "border-danger/30 bg-danger-soft text-danger-ink",
} as const;

export function StatusBadge({ kind, value }: { kind: StatusKind; value: string | null | undefined }) {
  const s = statusLabel(kind, value);
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASS[s.tone]}`}
      data-tone={s.tone}
      data-status={value ?? ""}
      title={s.known ? undefined : `Unrecognized status "${value ?? ""}"`}
    >
      {s.label}
    </span>
  );
}
