import Link from "next/link";

import type { Program } from "@/lib/api-types";
import { formatCount, formatDate, formatPercent } from "@/lib/format";
import { programMetrics } from "@/lib/programs";

import { StatusBadge } from "./StatusBadge";

const th = "px-3 py-2 text-left text-xs font-medium text-ink-muted";
const num = "px-3 py-2 text-right tabular-nums";

export function ProgramsTable({ programs }: { programs: readonly Program[] }) {
  return (
    <>
      <ul className="grid gap-2 md:hidden" aria-label="Recall programs">
        {programs.map((p) => {
          const m = programMetrics(p);
          return (
            <li key={p.id}>
              <Link href={`/app/programs/${p.id}`} className="block rounded-lg border border-line bg-surface p-3 text-sm focus-visible:outline-2 focus-visible:outline-accent">
                <span className="flex items-start justify-between gap-2">
                  <span className="font-medium text-accent">{p.title}</span>
                  <StatusBadge kind="program" value={p.status} />
                </span>
                <span className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <span className="text-ink-muted">Units affected</span>
                  <span className="text-right tabular-nums">{formatCount(p.units_affected)}</span>
                  <span className="text-ink-muted">Claims filed</span>
                  <span className="text-right tabular-nums">{formatCount(m.filed)}</span>
                  <span className="text-ink-muted">Awaiting review</span>
                  <span className="text-right tabular-nums">{formatCount(m.awaitingReview)}</span>
                  <span className="text-ink-muted">Corrected</span>
                  <span className="text-right tabular-nums">
                    {formatCount(m.corrected)} ({formatPercent(m.correctionRate)})
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="hidden overflow-x-auto rounded-lg border border-line bg-surface md:block">
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
    </>
  );
}
