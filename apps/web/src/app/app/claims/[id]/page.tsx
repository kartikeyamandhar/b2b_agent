import Link from "next/link";

import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadClaim, loadTrace } from "@/lib/api/queries";

export const dynamic = "force-dynamic";

export default async function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [claim, trace] = await Promise.all([loadClaim(id), loadTrace(id)]);
  const loadedAt = Date.now();
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href={claim.ok ? `/app/programs/${claim.data.program_id}/claims` : "/app/programs"} className="text-accent hover:underline">
          ← {claim.ok ? "Claims queue" : "Programs"}
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
          <ClaimDetailLive initialClaim={claim.data} initialTrace={trace.ok ? trace.data : null} loadedAt={loadedAt} />
        </>
      ) : (
        <ProblemAlert problem={claim.problem} />
      )}
    </div>
  );
}
