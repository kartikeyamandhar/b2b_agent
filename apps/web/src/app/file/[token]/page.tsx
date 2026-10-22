import type { ReactNode } from "react";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ClaimHeader, LinkNotActive, RecallContact } from "@/components/portal/RecallFacts";
import { RememberedClaim, StartClaim } from "@/components/portal/StartClaim";
import { loadIntake } from "@/lib/intake";
import { REFUND_CHOICE, claimPhotoKinds, listJoin, photoButtonLabel, photoHint, remedyPromise } from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

export const dynamic = "force-dynamic";

function Fact({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </div>
  );
}

/** A product's public claim link (plan v3): the product's public facts and one action, Start my claim. Starting gives
 *  the claimant a private link of their own; this page never shows anyone's claim. */
export default async function ProductLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const intake = await loadIntake(token);
  if (!intake.ok) {
    if (intake.problem.status === 404) {
      return (
        <div className="grid gap-3">
          <LinkNotActive />
          <RememberedClaim token={token} />
        </div>
      );
    }
    return <ProblemAlert problem={intake.problem} sentence={portalSentence(intake.problem.status)} showDetail={false} audience="claimant" />;
  }
  const v = intake.data;
  return (
    <div>
      <ClaimHeader companyName={v.company_name} title={v.title} recallNumber={v.recall_number} />
      <div className="grid gap-5 rounded-xl border border-line bg-surface p-5 sm:p-7">
        <Fact title="Which products">
          {v.models.length ? (
            <ul className="flex flex-wrap gap-2" aria-label="Recalled models">
              {v.models.map((m) => (
                <li key={m} className="rounded border border-line-strong px-2 py-1 font-mono text-sm">
                  {m}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-ink-muted">The notice does not list model numbers.</p>
          )}
          {v.model_label_location ? <p>The model number is {v.model_label_location}.</p> : null}
        </Fact>
        {v.sold_at.length ? (
          <Fact title="Where it was sold">
            <p>{listJoin(v.sold_at)}</p>
          </Fact>
        ) : null}
        <Fact title="What you get">
          <p>{remedyPromise(v.remedy_type)}</p>
          {v.remedy_type === "refund" ? <p className="text-sm text-ink-muted">{REFUND_CHOICE}</p> : null}
        </Fact>
        <Fact title="Photos your claim needs">
          <ul className="grid gap-2" data-intake-photos>
            {claimPhotoKinds(v.required_photos).map((kind) => (
              <li key={kind} className="rounded border border-line bg-panel p-3">
                <span className="font-medium">{photoButtonLabel(kind)}.</span> {photoHint(kind, v)}
              </li>
            ))}
          </ul>
        </Fact>
        <p className="text-sm text-ink-muted">Starting a claim gives you a private link to it. Keep that link: it is the only way back to your claim.</p>
        <StartClaim token={token} />
      </div>
      <RecallContact contact={v.contact} />
    </div>
  );
}
