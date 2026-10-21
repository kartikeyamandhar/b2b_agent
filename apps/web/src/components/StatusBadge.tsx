import { statusLabel, type StatusKind } from "@/lib/status";

const TONE_CLASS = {
  neutral: "border-line text-ink-muted",
  progress: "border-accent/40 text-accent",
  done: "border-line-strong text-ink",
  attention: "border-warn/50 bg-warn-soft text-warn-ink",
  negative: "border-line-strong font-semibold text-ink",
} as const;

export function StatusBadge({ kind, value }: { kind: StatusKind; value: string | null | undefined }) {
  const s = statusLabel(kind, value);
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-medium ${TONE_CLASS[s.tone]}`}
      data-tone={s.tone}
      data-status={value ?? ""}
      title={s.known ? undefined : `Unrecognized status "${value ?? ""}"`}
    >
      {s.label}
    </span>
  );
}
