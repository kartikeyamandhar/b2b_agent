import type { Metadata } from "next";
import Link from "next/link";

import { Campaign } from "@/components/Campaign";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadCampaign, loadProgram } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Owner notices" };

export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [program, campaign, role] = await Promise.all([loadProgram(id), loadCampaign(id), currentConsoleRole()]);
  return (
    <div className="grid grid-cols-1 gap-6">
      <nav aria-label="Breadcrumb" className="text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Products
        </Link>
        {program.ok ? (
          <>
            <span className="mx-1 text-ink-muted">/</span>
            <Link href={`/app/programs/${program.data.id}`} className="text-accent hover:underline">
              {program.data.title}
            </Link>
          </>
        ) : null}
      </nav>
      <header>
        <h1 className="page-title">Owner notices</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">Reach the owners you already know about: registration cards, warranty lists, and loyalty records.</p>
      </header>
      {!program.ok ? (
        <ProblemAlert problem={program.problem} />
      ) : !campaign.ok ? (
        <ProblemAlert problem={campaign.problem} />
      ) : (
        <Campaign
          key={program.data.id}
          programId={program.data.id}
          programStatus={program.data.status}
          initial={campaign.data}
          canAdmin={role === "admin"}
          exampleModel={program.data.identification_rules.models?.[0]}
        />
      )}
    </div>
  );
}
