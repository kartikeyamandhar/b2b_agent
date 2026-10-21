import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramOverview } from "@/components/ProgramOverview";
import { loadProgram } from "@/lib/api/queries";

export const dynamic = "force-dynamic";

export default async function ProgramPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await loadProgram(id);
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Programs
        </Link>
      </nav>
      {result.ok ? <ProgramOverview program={result.data} /> : <ProblemAlert problem={result.problem} />}
    </div>
  );
}
