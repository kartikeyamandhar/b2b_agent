import type { MprReport } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { MprPayload, boxLabel, boxSource, boxValue, orderedBoxes, periodLabel } from "@/lib/mpr";

const SOURCE_LABEL = { computed: "from claims", manual: "entered by hand", default: "default" } as const;

/** A generated MPR: the CPSC boxes in form order, where each value came from, and the reconciliation result. */
export function MprReportView({ programId, report }: { programId: string; report: MprReport }) {
  const parsed = MprPayload.safeParse(report.payload);
  const payload = parsed.success ? parsed.data : MprPayload.parse({});
  const discrepancies = payload.reconciliation?.discrepancies ?? [];
  const period = payload.period ?? report.period_start.slice(0, 7);
  return (
    <section aria-labelledby="mpr-title" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="mpr-title" className="section-title">
          Report for {periodLabel(period)}
        </h2>
        <span
          data-reconciled={report.reconciled}
          className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${report.reconciled ? "border-success/30 bg-success-soft text-success-ink" : "border-warn/40 bg-warn-soft text-warn-ink"}`}
        >
          {report.reconciled ? "Reconciled: the independent recount agrees" : `Not reconciled: ${discrepancies.length} ${discrepancies.length === 1 ? "discrepancy" : "discrepancies"}`}
        </span>
        <span className="text-xs text-ink-muted">generated {formatDateTime(report.generated_at)}</span>
        <a
          href={`/api/v1/programs/${programId}/mpr/${report.id}.pdf`}
          download={`mpr-${period}.pdf`}
          className="ml-auto rounded-md border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:border-ink"
          data-mpr-pdf
        >
          Download PDF
        </a>
      </div>
      {!parsed.success ? (
        <p role="alert" className="text-sm text-warn-ink">
          The report payload did not have the expected shape; the PDF is still available.
        </p>
      ) : null}
      {discrepancies.length ? (
        <details className="rounded border border-warn/50 bg-warn-soft p-2 text-xs text-warn-ink" open>
          <summary className="cursor-pointer font-medium">Recount discrepancies</summary>
          <pre className="mt-1 whitespace-pre-wrap font-mono">{JSON.stringify(discrepancies, null, 2)}</pre>
        </details>
      ) : null}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">MPR boxes in CPSC form order</caption>
          <thead className="border-b border-line bg-panel text-left text-xs text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Box</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
              <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">Source</th>
            </tr>
          </thead>
          <tbody>
            {orderedBoxes(payload.boxes).map(([k, v]) => {
              const src = boxSource(k, payload);
              return (
                <tr key={k} className="border-b border-line last:border-b-0" data-box={k}>
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {boxLabel(k)}
                    <span className="block text-xs text-ink-muted sm:hidden">{SOURCE_LABEL[src]}</span>
                  </th>
                  <td className={`px-3 py-1.5 text-right tabular-nums ${typeof v === "number" || typeof v === "boolean" ? "whitespace-nowrap" : "[overflow-wrap:anywhere]"}`}>{boxValue(v)}</td>
                  <td className={`hidden px-3 py-1.5 text-xs sm:table-cell ${src === "manual" ? "text-warn-ink" : "text-ink-muted"}`}>{SOURCE_LABEL[src]}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {payload.notes.length ? (
        <details className="text-xs text-ink-muted">
          <summary className="cursor-pointer">How the boxes are counted</summary>
          <ul className="mt-1 list-disc pl-5">
            {payload.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
