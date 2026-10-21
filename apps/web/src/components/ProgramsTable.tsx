import Link from "next/link";

import type { Program } from "@/lib/api-types";
import { formatCount, formatDate, formatPercent } from "@/lib/format";
import { programMetrics } from "@/lib/programs";

import { StatusBadge } from "./StatusBadge";

const th = "px-3 py-2 text-left text-xs font-medium text-ink-muted";
const num = "px-3 py-2 text-right tabular-nums";

export function ProgramsTable({ programs }: { programs: readonly Program[] }) {
  return (
    <div className="overflow-x-auto rounded border border-line">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <caption className="sr-only">Recall programs</caption>
        <thead className="border-b border-line bg-panel">
          <tr>
            <th scope="col" className={th}>Program</th>
            <th scope="col" className={th}>Status</th>
            <th scope="col" className={`${th} text-right`}>Units affected</th>
            <th scope="col" className={`${th} text-right`}>Claims filed</th>
            <th scope="col" className={`${th} text-right`}>Awaiting review</th>
            <th scope="col" className={`${th} text-right`}>Corrected</th>
            <th scope="col" className={th}>Opened</th>
          </tr>
        </thead>
        <tbody>
          {programs.map((p) => {
            const m = programMetrics(p);
            return (
              <tr key={p.id} className="border-b border-line last:border-b-0 hover:bg-panel">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  <Link href={`/app/programs/${p.id}`} className="text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">
                    {p.title}
                  </Link>
                </th>
                <td className="px-3 py-2">
                  <StatusBadge kind="program" value={p.status} />
                </td>
                <td className={num}>{formatCount(p.units_affected)}</td>
                <td className={num}>{formatCount(m.filed)}</td>
                <td className={num}>{formatCount(m.awaitingReview)}</td>
                <td className={num}>
                  {formatCount(m.corrected)}
                  <span className="ml-1 text-xs text-ink-muted">({formatPercent(m.correctionRate)})</span>
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{p.opened_at ? formatDate(p.opened_at) : "Not opened"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
