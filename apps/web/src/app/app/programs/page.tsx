import type { Metadata } from "next";
import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramsTable } from "@/components/ProgramsTable";
import { EmptyState } from "@/components/States";
import { loadPrograms } from "@/lib/api/queries";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Programs" };

export default async function ProgramsPage() {
  const result = await loadPrograms();
  return (
    <section aria-labelledby="programs-title">
      <h1 id="programs-title" className="mb-4 text-lg font-semibold">
        Programs
      </h1>
      {!result.ok ? (
        <ProblemAlert problem={result.problem} />
      ) : result.data.items.length === 0 ? (
        <EmptyState title="No recall programs yet">
          A program is opened from a recall in the public archive. Opening one from this console comes in a later
          phase.
        </EmptyState>
      ) : (
        <ProgramsTable programs={result.data.items} />
      )}
    </section>
  );
}
