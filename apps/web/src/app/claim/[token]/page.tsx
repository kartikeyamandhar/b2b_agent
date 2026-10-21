import { redirect } from "next/navigation";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ClaimStepper } from "@/components/portal/ClaimStepper";
import { loadPortal } from "@/lib/api/queries";
import { portalSentence } from "@/lib/portal-copy";

export const dynamic = "force-dynamic";

export default async function ClaimFormPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const view = await loadPortal(token);
  if (!view.ok) return <ProblemAlert problem={view.problem} sentence={portalSentence(view.problem.status)} />;
  // Submitted claims cannot be edited: the status page is where the claimant goes from here.
  if (view.data.status !== "draft") redirect(`/claim/${encodeURIComponent(token)}/status`);
  return (
    <div>
      <h1 className="mb-4 text-2xl font-semibold">{view.data.program.title}</h1>
      <ClaimStepper token={token} view={view.data} />
    </div>
  );
}
