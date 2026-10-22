import type { Metadata } from "next";
import Link from "next/link";

import { NewProgram } from "@/components/NewProgram";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadAccess, loadPrograms } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";
import { programRecallIds } from "@/lib/program-create";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "New program" };

export default async function NewProgramPage() {
  const [programs, role, access] = await Promise.all([loadPrograms(), currentConsoleRole(), loadAccess()]);
  return (
    <div className="grid gap-6">
      <nav aria-label="Breadcrumb" className="text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Programs
        </Link>
      </nav>
      <header>
        <h1 className="page-title">New recall program</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">Start a program from a CPSC recall. You review the rules before anything reaches claimants.</p>
      </header>
      {role !== "admin" ? (
        <ProblemAlert
          problem={{ status: 403, title: "Forbidden", detail: null, correlationId: null, retryAfterSeconds: null }}
          sentence="Only an admin can create a recall program."
        />
      ) : !programs.ok ? (
        <ProblemAlert problem={programs.problem} />
      ) : (
        <NewProgram existingRecallIds={[...programRecallIds(programs.data.items)]} typedNeedsApproval={!access.ok || access.data.required} />
      )}
    </div>
  );
}
