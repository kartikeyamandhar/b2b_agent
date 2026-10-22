import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ClaimHeader, RecallContact } from "@/components/portal/RecallFacts";
import { StatusView } from "@/components/portal/StatusView";
import { loadPortal } from "@/lib/api/queries";
import { portalSentence } from "@/lib/portal-copy";

export const dynamic = "force-dynamic";

export default async function ClaimStatusPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const view = await loadPortal(token);
  if (!view.ok) return <ProblemAlert problem={view.problem} sentence={portalSentence(view.problem.status)} showDetail={false} audience="claimant" />;
  const program = view.data.program;
  return (
    <div>
      <ClaimHeader companyName={program.company_name} title={program.title} />
      {view.data.status === "draft" ? (
        <p className="text-base">
          Your claim is not submitted yet.{" "}
          <Link href={`/claim/${encodeURIComponent(token)}`} className="text-accent underline">
            Continue your claim
          </Link>
          .
        </p>
      ) : (
        <StatusView token={token} view={view.data} />
      )}
      <RecallContact contact={program.contact} />
    </div>
  );
}
