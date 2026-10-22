"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { z } from "zod";

import { ExportBatchSummary } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { countOf, formatCents, formatCount, formatDateTime } from "@/lib/format";
import { EXPORT_LIST_LIMIT, batchAuthor, exportCsvHref, exportFilename, exportTotals } from "@/lib/payouts";

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
    <a href={href} download={exportFilename(batch)} className="inline-block whitespace-nowrap rounded-md border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:bg-panel" data-download={batch.id}>
      Download CSV
    </a>
  );
}

/** The batch history: a table from lg, cards below it. Under lg the sidebar leaves the table too little room, so it
 *  would scroll sideways; the fingerprint column waits for xl, and the cards always show it. */
export function ExportBatchTable({ items }: { items: readonly ExportBatchSummary[] }) {
  if (items.length === 0) {
    return (
      <EmptyState title="No export batches yet">
        When a claimant chooses a mailed check, or does not finish a bank transfer by the deadline, their approved refund
        waits for a batch you create here for your bank.
      </EmptyState>
    );
  }
  return (
    <>
      <ul className="grid grid-cols-1 gap-2 lg:hidden">
        {items.map((b) => (
          <li key={b.id} className="min-w-0 rounded-lg border border-line bg-surface p-3 text-sm" data-batch={b.id}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">{formatCents(b.total_cents)}</p>
                <p className="text-xs text-ink-muted">
                  {countOf(b.row_count, "payout")} · {formatDateTime(b.created_at)}
                </p>
                <p className="text-xs text-ink-muted [overflow-wrap:anywhere]">{batchAuthor(b.created_by)}</p>
              </div>
              <Download batch={b} />
            </div>
            <p className="mt-2 truncate font-mono text-[11px] text-ink-muted" title={b.sha256}>
              sha256 {b.sha256}
            </p>
          </li>
        ))}
      </ul>
      {/* relative: the header's sr-only label is absolutely positioned, and without a positioned scroller it escaped
          the overflow and widened the whole page. */}
      <div className="relative hidden overflow-x-auto rounded-lg border border-line bg-surface lg:block">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Export batches, newest first</caption>
          <thead className="border-b border-line bg-panel">
            <tr>
              {["Created", "By", "Payouts", "Total", "File fingerprint (sha256)", ""].map((h, i) => (
                <th key={i} scope="col" className={`px-3 py-2 text-xs font-medium whitespace-nowrap text-ink-muted ${i === 2 || i === 3 ? "text-right" : "text-left"} ${i === 4 ? "hidden xl:table-cell" : ""}`}>
                  {h || <span className="sr-only">Download</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((b) => (
              <tr key={b.id} className="border-b border-line last:border-b-0" data-batch={b.id}>
                <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(b.created_at)}</td>
                <td className="px-3 py-2">
                  {/* The cap sits on the span: a table cell's own max-width is not reliably honoured. */}
                  <span className="block max-w-40 truncate whitespace-nowrap" title={batchAuthor(b.created_by)}>
                    {batchAuthor(b.created_by)}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(b.row_count)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCents(b.total_cents)}</td>
                <td className="hidden max-w-56 px-3 py-2 xl:table-cell">
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
              Batch created: {countOf(result.batch.row_count, "payout")}, {formatCents(result.batch.total_cents)}.
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

export { EXPORT_LIST_LIMIT };

/**
 * Totals of the listed batches. They are all-time only while the list is shorter than its limit; at the limit,
 * older batches exist that the list does not include, so the labels say the totals cover the batches shown.
 */
export function ExportSummary({ items, limit = EXPORT_LIST_LIMIT }: { items: readonly ExportBatchSummary[]; limit?: number }) {
  const t = exportTotals(items);
  const partial = items.length >= limit;
  return (
    <div className="grid gap-2" data-export-summary={partial ? "shown" : "all"}>
      {/* Three across from lg (the sidebar leaves too little room below it); values pinned to each tile's bottom. */}
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {(
          [
            [partial ? "Batches shown" : "Batches", formatCount(t.batches)],
            [partial ? "Payouts in these batches" : "Payouts exported", formatCount(t.rows)],
            [partial ? "Total in these batches" : "Total exported", formatCents(t.cents)],
          ] as const
        ).map(([label, value], i) => (
          // Two across below lg would leave the third tile alone beside an empty cell; it takes the full row instead.
          <div key={label} className={`flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-4 ${i === 2 ? "col-span-2 lg:col-span-1" : ""}`}>
            <dt className="eyebrow">{label}</dt>
            <dd className="kpi-value mt-auto pt-2 break-words">{value}</dd>
          </div>
        ))}
      </dl>
      {partial ? (
        <p className="text-xs text-ink-muted">
          These totals cover the {formatCount(items.length)} newest batches. Older batches are not counted.
        </p>
      ) : null}
    </div>
  );
}
