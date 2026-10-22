import type { Metadata } from "next";
import Link from "next/link";

import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadClaim, loadProgram, loadTrace } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Claim" };

export default async function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [claim, trace, role] = await Promise.all([loadClaim(id), loadTrace(id), currentConsoleRole()]);
  // The program's status decides whether a link can be reissued; if it cannot be read, the API still decides (409).
  const program = claim.ok && role === "admin" ? await loadProgram(claim.data.program_id) : null;
  const loadedAt = Date.now();
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href={claim.ok ? `/app/programs/${claim.data.program_id}/claims` : "/app/programs"} className="text-accent hover:underline">
          ← {claim.ok ? "Claims queue" : "Products"}
        </Link>
      </nav>
      {claim.ok ? (
        <>
          {!trace.ok ? (
            <div className="mb-3">
              <p className="mb-1 text-xs text-warn-ink">The run could not be loaded; photo readings and duplicate hits are missing.</p>
              <ProblemAlert problem={trace.problem} />
            </div>
          ) : null}
          <ClaimDetailLive initialClaim={claim.data} initialTrace={trace.ok ? trace.data : null} loadedAt={loadedAt} canAdmin={role === "admin"} programStatus={program?.ok ? program.data.status : null} />
        </>
      ) : (
        <div className="grid gap-4">
          <h1 className="page-title">Claim</h1>
          <ProblemAlert problem={claim.problem} />
        </div>
      )}
    </div>
  );
}
