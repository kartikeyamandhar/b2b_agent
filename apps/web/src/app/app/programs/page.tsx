import type { Metadata } from "next";
import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { CARD_LIMIT, PortfolioSummary, ProgramCards } from "@/components/ProgramPortfolio";
import { ProgramsTable } from "@/components/ProgramsTable";
import { EmptyState } from "@/components/States";
import { loadPrograms } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Programs" };

export default async function ProgramsPage() {
  const [result, role] = await Promise.all([loadPrograms(), currentConsoleRole()]);
  const canCreate = role === "admin";
  return (
    <section aria-labelledby="programs-title" className="grid gap-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <h1 id="programs-title" className="page-title">
            Programs
          </h1>
          <p className="mt-2 max-w-2xl text-ink-muted">Each recall your firm runs, its claims, and how close it is to correcting every affected unit.</p>
        </div>
        {canCreate ? (
          <Link href="/app/programs/new" className="self-start rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 sm:self-auto" data-new-program>
            New program
          </Link>
        ) : null}
      </header>
      {!result.ok ? (
        <ProblemAlert problem={result.problem} />
      ) : result.data.items.length === 0 ? (
        <EmptyState title="No recall programs yet">
          A program starts from a recall in the public CPSC archive.{" "}
          {canCreate ? <Link href="/app/programs/new">Create the first program</Link> : "An admin creates the first program."}
        </EmptyState>
      ) : (
        <>
          <PortfolioSummary programs={result.data.items} />
          {result.data.items.length <= CARD_LIMIT ? <ProgramCards programs={result.data.items} /> : <ProgramsTable programs={result.data.items} />}
        </>
      )}
    </section>
  );
}
