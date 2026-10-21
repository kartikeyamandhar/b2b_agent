import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { TraceLive } from "@/components/TraceLive";
import { shortId } from "@/lib/claims";
import { loadClaim, loadTrace } from "@/lib/api/queries";

export const dynamic = "force-dynamic";

export default async function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [claim, trace] = await Promise.all([loadClaim(id), loadTrace(id)]);
  const loadedAt = Date.now();
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href={`/app/claims/${id}`} className="text-accent hover:underline">
          ← Claim {claim.ok ? shortId(claim.data.id) : ""}
        </Link>
      </nav>
      <h1 className="mb-3 text-lg font-semibold">Agent trace</h1>
      {!claim.ok ? (
        <ProblemAlert problem={claim.problem} />
      ) : !trace.ok ? (
        <ProblemAlert problem={trace.problem} />
      ) : (
        <TraceLive claimId={id} initialTrace={trace.data} cited={claim.data.decision?.cited_rules ?? []} loadedAt={loadedAt} />
      )}
    </div>
  );
}
