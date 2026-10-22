import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

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
  // The same frame as the fraud, settings, and campaign pages: breadcrumb, heading, then the content or the alert.
  const frame = (body: ReactNode) => (
    <div className="grid grid-cols-1 gap-6">
      <nav aria-label="Breadcrumb" className="text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Products
        </Link>
        {program.ok ? (
          <>
            <span className="mx-1 text-ink-muted">/</span>
            <Link href={`/app/programs/${program.data.id}`} className="text-accent hover:underline">
              {program.data.title}
            </Link>
          </>
        ) : null}
      </nav>
      <header>
        <h1 className="page-title">Monthly progress reports</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">
          The CPSC monthly progress report, counted from this product&apos;s claims and checked by an independent recount.
        </p>
      </header>
      {body}
    </div>
  );
  if (!program.ok) return frame(<ProblemAlert problem={program.problem} />);
  const periods = reportPeriods(program.data.opened_at ?? program.data.created_at, new Date());
  const requested = typeof sp.period === "string" ? sp.period : undefined;
  const period = isPeriod(requested) ? requested : periods[0]!;
  const report = await loadMpr(id, period);

  return frame(
    <>
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
      {/* Keyed by period: the boxes the firm types belong to one month and never carry over to the next. */}
      <MprGenerate key={period} programId={id} period={period} canGenerate={role === "admin"} hasReport={report.ok && !!report.data} />
    </>,
  );
}
