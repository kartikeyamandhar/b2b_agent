"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { z } from "zod";

import { ExportBatchSummary } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { formatCents, formatCount, formatDateTime } from "@/lib/format";
import { batchAuthor, exportCsvHref, exportFilename, exportTotals } from "@/lib/payouts";

import { ProblemAlert } from "./ProblemAlert";
import { EmptyState } from "./States";

const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";

const CreateResult = ExportBatchSummary.nullable();

const asProblem = (err: unknown): ApiProblem =>
  err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };

function Download({ batch }: { batch: ExportBatchSummary }) {
  const href = exportCsvHref(batch.id);
  if (!href) return <span className="text-xs text-ink-muted">Unavailable</span>;
  return (
    <a href={href} download={exportFilename(batch)} className="inline-block rounded-md border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:bg-panel" data-download={batch.id}>
      Download CSV
    </a>
  );
}

/** The batch history: a table on wide screens, cards on phones. */
export function ExportBatchTable({ items }: { items: readonly ExportBatchSummary[] }) {
  if (items.length === 0) {
    return (
      <EmptyState title="No export batches yet">
        When claimants choose a mailed check, their approved refunds wait here until you create a batch for your bank.
      </EmptyState>
    );
  }
  return (
    <>
      <ul className="grid grid-cols-1 gap-2 sm:hidden">
        {items.map((b) => (
          <li key={b.id} className="min-w-0 rounded-lg border border-line bg-surface p-3 text-sm" data-batch={b.id}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-medium">{formatCents(b.total_cents)}</p>
                <p className="text-xs text-ink-muted">
                  {formatCount(b.row_count)} payouts · {formatDateTime(b.created_at)}
                </p>
                <p className="text-xs text-ink-muted">{batchAuthor(b.created_by)}</p>
              </div>
              <Download batch={b} />
            </div>
            <p className="mt-2 truncate font-mono text-[11px] text-ink-muted" title={b.sha256}>
              sha256 {b.sha256}
            </p>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto rounded-lg border border-line bg-surface sm:block">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Export batches, newest first</caption>
          <thead className="border-b border-line bg-panel">
            <tr>
              {["Created", "By", "Payouts", "Total", "File fingerprint (sha256)", ""].map((h, i) => (
                <th key={i} scope="col" className={`px-3 py-2 text-xs font-medium text-ink-muted ${i === 2 || i === 3 ? "text-right" : "text-left"}`}>
                  {h || <span className="sr-only">Download</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((b) => (
              <tr key={b.id} className="border-b border-line last:border-b-0" data-batch={b.id}>
                <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(b.created_at)}</td>
                <td className="px-3 py-2">{batchAuthor(b.created_by)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(b.row_count)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCents(b.total_cents)}</td>
                <td className="max-w-56 px-3 py-2">
                  <code className="block truncate font-mono text-xs text-ink-muted" title={b.sha256}>
                    {b.sha256}
                  </code>
                </td>
                <td className="px-3 py-2 text-right">
                  <Download batch={b} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/**
 * Create an export batch (POST /v1/payouts/exports, admin). It pays every approved refund waiting for a check in one
 * file and marks those claims paid, so it is confirmed inline first. "Nothing to export" is an answer, not an error.
 */
export function CreateExport() {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [result, setResult] = useState<{ batch: ExportBatchSummary | null } | null>(null);

  async function create() {
    setBusy(true);
    setProblem(null);
    try {
      const batch = await sendJson(CreateResult as z.ZodType<ExportBatchSummary | null>, "POST", "/v1/payouts/exports", {});
      setResult({ batch });
      setConfirming(false);
      router.refresh();
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3" data-create-export>
      {!confirming ? (
        <div>
          <button type="button" onClick={() => { setConfirming(true); setResult(null); }} className={PRIMARY} data-create-export-start>
            Create export batch
          </button>
        </div>
      ) : (
        <div role="group" aria-label="Confirm export batch" className="grid gap-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink">
          <p className="font-medium">Create a batch of every refund waiting for a check?</p>
          <p>
            Each payout in the batch is marked exported and its claim is marked paid. This cannot be undone from the
            console: send the file to your bank once, then keep it with the fingerprint shown here.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void create()} disabled={busy} className={PRIMARY} data-create-export-confirm>
              {busy ? "Creating…" : "Create batch"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={SECONDARY}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {result ? (
        result.batch ? (
          <div role="status" className="grid gap-2 rounded-md border border-success/40 bg-success-soft p-3 text-sm text-success-ink" data-export-created>
            <p className="font-medium">
              Batch created: {formatCount(result.batch.row_count)} payouts, {formatCents(result.batch.total_cents)}.
            </p>
            <div>
              <Download batch={result.batch} />
            </div>
          </div>
        ) : (
          <p role="status" className="text-sm text-ink-muted" data-export-empty>
            Nothing to export: no approved refund is waiting for a check.
          </p>
        )
      ) : null}
      {problem ? <ProblemAlert problem={problem} /> : null}
    </div>
  );
}

export function ExportSummary({ items }: { items: readonly ExportBatchSummary[] }) {
  const t = exportTotals(items);
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {(
        [
          ["Batches", formatCount(t.batches)],
          ["Payouts exported", formatCount(t.rows)],
          ["Total exported", formatCents(t.cents)],
        ] as const
      ).map(([label, value]) => (
        <div key={label} className="min-w-0 rounded-lg border border-line bg-surface px-4 py-4">
          <dt className="eyebrow">{label}</dt>
          <dd className="kpi-value mt-2 [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
