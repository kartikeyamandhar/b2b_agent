import type { Metadata } from "next";
import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { CARD_LIMIT, PortfolioSummary, ProgramCards } from "@/components/ProgramPortfolio";
import { ProgramsTable } from "@/components/ProgramsTable";
import { EmptyState } from "@/components/States";
import { loadPrograms } from "@/lib/api/queries";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Products" };

/** The company's products (plan v3). A product is never created here: the operator's approval of a recall request
 *  under Recall access creates and opens it. */
export default async function ProgramsPage() {
  const result = await loadPrograms();
  return (
    <section aria-labelledby="programs-title" className="grid gap-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <h1 id="programs-title" className="page-title">
            Products
          </h1>
          <p className="mt-2 max-w-2xl text-ink-muted">Each recall your company runs, its claims, and how close it is to correcting every affected unit.</p>
        </div>
        {result.ok && result.data.items.length > 0 ? (
          <Link href="/app/access" className="self-start rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-ink sm:self-auto" data-ask-recall>
            Ask for another recall
          </Link>
        ) : null}
      </header>
      {!result.ok ? (
        <ProblemAlert problem={result.problem} />
      ) : result.data.items.length === 0 ? (
        <EmptyState title="No products yet">
          Ask for a recall under <Link href="/app/access">Recall access</Link>; when the operator approves it, your product opens here with its claim
          link.
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
