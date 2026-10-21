// Owner notification campaigns (M5): upload registrations (CSV, synthetic rows only), match them to the program's
// rules, draft notices per channel, and mark them sent. Sending is simulated: no message leaves RecallOps, and the
// MPR's notified_* boxes count what was marked sent (campaigns.py).
import type { CampaignSummary } from "@/lib/api-types";

export const CHANNELS = ["email", "mail", "phone_text"] as const;
export type Channel = (typeof CHANNELS)[number];

const CHANNEL_LABEL: Record<string, string> = { email: "Email", mail: "Mail", phone_text: "Text message" };
export function channelLabel(c: string): string {
  return Object.hasOwn(CHANNEL_LABEL, c) ? CHANNEL_LABEL[c]! : c.replace(/_/g, " ");
}

/** The API's limits (settings.registrations_max_bytes); checked here first so a large file is not uploaded at all. */
export const MAX_CSV_BYTES = 5_000_000;
export const COLUMNS = ["external_ref", "name", "email", "phone", "address", "model", "serial", "date_code", "purchase_date"] as const;

/** A starting file with the columns the API reads and two synthetic owners (example.com email, 555-01xx phone). */
export function templateCsv(model = "MODEL-123"): string {
  // Quoted, and a leading formula character neutralised, so a model number can never break or script the file.
  const cell = `"${model.replace(/^[=+\-@\t\r]/, "'$&").replace(/"/g, '""')}"`;
  const rows = [
    COLUMNS.join(","),
    `OWNER-0001,Sample Owner One,owner.one@example.com,555-0101,"1 Example Street, Springfield",${cell},,,2026-03-15`,
    `OWNER-0002,Sample Owner Two,,555-0102,"2 Example Avenue, Springfield",${cell},,,`,
  ];
  return rows.join("\n") + "\n";
}

export type FileCheck = { ok: true } | { ok: false; reason: string };

export function checkCsvFile(file: { name: string; size: number; type: string } | null): FileCheck {
  if (!file) return { ok: false, reason: "Choose a CSV file." };
  if (!/\.csv$/i.test(file.name) && file.type !== "text/csv") return { ok: false, reason: "Choose a .csv file." };
  if (file.size === 0) return { ok: false, reason: "The file is empty." };
  if (file.size > MAX_CSV_BYTES) return { ok: false, reason: "The file is larger than 5 MB. Split it and upload each part." };
  return { ok: true };
}

export type RejectedRow = { row: number | null; reason: string };

export function rejectedRows(rows: readonly Record<string, unknown>[]): RejectedRow[] {
  return rows.map((r) => ({
    row: typeof r.row === "number" && Number.isFinite(r.row) ? r.row : null,
    reason: typeof r.reason === "string" && r.reason ? r.reason : "rejected",
  }));
}

export type ChannelRow = { channel: string; draft: number; sent: number; other: number };

/** notifications {"channel:status": n} as one row per channel (known channels first, zeros kept). */
export function notificationRows(notifications: Record<string, number>): ChannelRow[] {
  const by = new Map<string, ChannelRow>(CHANNELS.map((c) => [c, { channel: c, draft: 0, sent: 0, other: 0 }]));
  for (const [key, n] of Object.entries(notifications)) {
    const [channel = key, status = ""] = key.split(":");
    const row = by.get(channel) ?? { channel, draft: 0, sent: 0, other: 0 };
    const count = Number.isFinite(n) && n > 0 ? n : 0;
    if (status === "draft") row.draft += count;
    else if (status === "sent") row.sent += count;
    else row.other += count;
    by.set(channel, row);
  }
  return [...by.values()];
}

export type Stage = "upload" | "match" | "draft" | "send" | "done";

/** The next useful step, from the summary alone. */
export function campaignStage(s: Pick<CampaignSummary, "registrations" | "matched" | "unmatched" | "notifications">): Stage {
  if (s.registrations === 0) return "upload";
  if (s.matched + s.unmatched < s.registrations) return "match";
  if (s.matched === 0) return "match";
  const rows = notificationRows(s.notifications);
  if (rows.every((r) => r.draft + r.sent === 0)) return "draft";
  if (rows.some((r) => r.draft > 0)) return "send";
  return "done";
}

export type SampleDraft = { channel: string; status: string; subject: string; body: string };

export function sampleDrafts(sample: readonly Record<string, unknown>[]): SampleDraft[] {
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  return sample.map((s) => ({ channel: str(s.channel), status: str(s.status), subject: str(s.subject), body: str(s.body) }));
}

/** A draft body split around {claim_link}, so the placeholder is shown as a marker and never as a real link. */
export function splitPlaceholder(body: string): { text: string; link: boolean }[] {
  return body.split(/(\{claim_link\})/).filter(Boolean).map((part) => ({ text: part, link: part === "{claim_link}" }));
}

export function sendTotal(rows: readonly ChannelRow[], channel: string | null): number {
  return rows.filter((r) => channel === null || r.channel === channel).reduce((n, r) => n + r.draft, 0);
}
