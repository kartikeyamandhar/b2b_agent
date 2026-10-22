import { redirect } from "next/navigation";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ClaimStepper } from "@/components/portal/ClaimStepper";
import { ClaimHeader, RecallContact } from "@/components/portal/RecallFacts";
import { loadPortal } from "@/lib/api/queries";
import { portalSentence } from "@/lib/portal-copy";

export const dynamic = "force-dynamic";

export default async function ClaimFormPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const view = await loadPortal(token);
  if (!view.ok) return <ProblemAlert problem={view.problem} sentence={portalSentence(view.problem.status)} showDetail={false} audience="claimant" />;
  // Submitted claims cannot be edited: the status page is where the claimant goes from here.
  if (view.data.status !== "draft") redirect(`/claim/${encodeURIComponent(token)}/status`);
  const program = view.data.program;
  return (
    <div>
      <ClaimHeader companyName={program.company_name} title={program.title} />
      <ClaimStepper token={token} view={view.data} />
      <RecallContact contact={program.contact} />
    </div>
  );
}
