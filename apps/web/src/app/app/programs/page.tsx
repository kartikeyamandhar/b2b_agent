import type { Metadata } from "next";

import { ProblemAlert } from "@/components/ProblemAlert";
import { CARD_LIMIT, PortfolioSummary, ProgramCards } from "@/components/ProgramPortfolio";
import { ProgramsTable } from "@/components/ProgramsTable";
import { EmptyState } from "@/components/States";
import { loadPrograms } from "@/lib/api/queries";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Programs" };

export default async function ProgramsPage() {
  const result = await loadPrograms();
  return (
    <section aria-labelledby="programs-title" className="grid gap-8">
      <header>
        <h1 id="programs-title" className="page-title">
          Programs
        </h1>
        <p className="mt-2 max-w-2xl text-ink-muted">Each recall your firm runs, its claims, and how close it is to correcting every affected unit.</p>
      </header>
      {!result.ok ? (
        <ProblemAlert problem={result.problem} />
      ) : result.data.items.length === 0 ? (
        <EmptyState title="No recall programs yet">
          A program is opened from a recall in the public archive. Opening one from this console comes in a later
          phase.
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
