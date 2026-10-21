import type { Metadata } from "next";
import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramOverview } from "@/components/ProgramOverview";
import { loadProgram } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Program" };

export default async function ProgramPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [result, role] = await Promise.all([loadProgram(id), currentConsoleRole()]);
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Programs
        </Link>
      </nav>
      {result.ok ? <ProgramOverview program={result.data} canAdmin={role === "admin"} /> : <ProblemAlert problem={result.problem} />}
    </div>
  );
}
