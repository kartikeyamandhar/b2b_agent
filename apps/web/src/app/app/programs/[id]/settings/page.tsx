import type { Metadata } from "next";
import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramSettings } from "@/components/ProgramSettings";
import { StatusBadge } from "@/components/StatusBadge";
import { loadProgram } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Product settings" };

export default async function ProgramSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [result, role] = await Promise.all([loadProgram(id), currentConsoleRole()]);
  return (
    <div className="grid grid-cols-1 gap-6">
      <nav aria-label="Breadcrumb" className="text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Products
        </Link>
        {result.ok ? (
          <>
            <span className="mx-1 text-ink-muted">/</span>
            <Link href={`/app/programs/${result.data.id}`} className="text-accent hover:underline">
              {result.data.title}
            </Link>
          </>
        ) : null}
      </nav>
      {/* The heading shows for a missing product too, as on the fraud, reports, and campaign pages. */}
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="page-title">Rules and settings</h1>
        {result.ok ? <StatusBadge kind="program" value={result.data.status} /> : null}
      </header>
      {result.ok ? <ProgramSettings key={result.data.id} program={result.data} canAdmin={role === "admin"} /> : <ProblemAlert problem={result.problem} />}
    </div>
  );
}
