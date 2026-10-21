// Payout export batches (GET/POST /v1/payouts/exports, GET /v1/payouts/exports/{id}.csv; admin only). A batch is
// the check or ACH file a firm's bank pays from: creating one marks its payouts exported and their claims paid in the
// same transaction (payouts/export.py), so the console confirms before it asks.
import type { ExportBatchSummary } from "@/lib/api-types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Same-origin download through the authenticated proxy; null for an id that cannot name a batch. */
export function exportCsvHref(batchId: string): string | null {
  return UUID.test(batchId) ? `/api/v1/payouts/exports/${batchId}.csv` : null;
}

/** A dated file name for the browser's save dialog, e.g. payout-export-2026-10-21-97532299.csv. */
export function exportFilename(batch: Pick<ExportBatchSummary, "id" | "created_at">): string {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(batch.created_at)?.[0] ?? "batch";
  return `payout-export-${day}-${batch.id.slice(0, 8)}.csv`;
}

/** Who wrote the batch: the seed and the scheduled job are named, people show their user id. */
export function batchAuthor(createdBy: string): string {
  if (createdBy === "seed") return "Demo data";
  if (createdBy === "export-job") return "Scheduled export job";
  return createdBy;
}

export type ExportTotals = { batches: number; rows: number; cents: number };

export function exportTotals(items: readonly Pick<ExportBatchSummary, "row_count" | "total_cents">[]): ExportTotals {
  return items.reduce((t, b) => ({ batches: t.batches + 1, rows: t.rows + b.row_count, cents: t.cents + b.total_cents }), { batches: 0, rows: 0, cents: 0 });
}
