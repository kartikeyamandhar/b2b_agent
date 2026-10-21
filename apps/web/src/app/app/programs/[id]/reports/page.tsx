import type { Metadata } from "next";
import Link from "next/link";

import { MprGenerate } from "@/components/MprGenerate";
import { MprReportView } from "@/components/MprReportView";
import { ProblemAlert } from "@/components/ProblemAlert";
import { EmptyState } from "@/components/States";
import { loadMpr, loadProgram } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";
import { isPeriod, periodLabel, reportPeriods } from "@/lib/mpr";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Monthly reports" };

type Props = { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function ReportsPage({ params, searchParams }: Props) {
  const { id } = await params;
  const sp = await searchParams;
  const [program, role] = await Promise.all([loadProgram(id), currentConsoleRole()]);
  if (!program.ok) return <ProblemAlert problem={program.problem} />;
  const periods = reportPeriods(program.data.opened_at ?? program.data.created_at, new Date());
  const requested = typeof sp.period === "string" ? sp.period : undefined;
  const period = isPeriod(requested) ? requested : periods[0]!;
  const report = await loadMpr(id, period);

  return (
    <div className="grid gap-4">
      <nav aria-label="Breadcrumb" className="flex flex-wrap gap-1 text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          Programs
        </Link>
        <span aria-hidden="true">/</span>
        <Link href={`/app/programs/${id}`} className="text-accent hover:underline">
          {program.data.title}
        </Link>
      </nav>
      <h1 className="page-title">Monthly progress reports</h1>
      <p className="text-sm text-ink-muted">
        The CPSC monthly progress report, counted from this program&apos;s claims and checked by an independent recount.
      </p>
      <nav aria-label="Report periods">
        <ul className="flex flex-wrap gap-1.5">
          {periods.map((p) => (
            <li key={p}>
              <Link
                href={`/app/programs/${id}/reports?period=${p}`}
                aria-current={p === period ? "page" : undefined}
                className={`inline-block rounded-md border px-2 py-1 text-xs ${p === period ? "border-accent bg-accent text-bg" : "border-line hover:bg-panel"} hover:opacity-90`}
              >
                {periodLabel(p)}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {!report.ok ? (
        <ProblemAlert problem={report.problem} />
      ) : report.data ? (
        <MprReportView programId={id} report={report.data} />
      ) : (
        <EmptyState title={`No report for ${periodLabel(period)} yet`}>Generate it below. Generating again later adds a newer version.</EmptyState>
      )}
      <MprGenerate programId={id} period={period} canGenerate={role === "admin"} hasReport={report.ok && !!report.data} />
    </div>
  );
}
