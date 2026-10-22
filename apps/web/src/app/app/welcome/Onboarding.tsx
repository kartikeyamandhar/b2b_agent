"use client";

import { useState } from "react";

import { ProblemAlert } from "@/components/ProblemAlert";
import type { OnboardingRequest, PlanInfo } from "@/lib/api-types";
import type { ApiProblem } from "@/lib/api/problem";
import { AFTER_ONBOARDING, submitOnboarding } from "@/lib/billing-actions";
import { COMPANY_NAME_MAX, onboardingBody, planCard, planLabel, type PlanCard } from "@/lib/billing";

type PlanId = OnboardingRequest["plan"];
const PLAN_IDS: readonly PlanId[] = ["free", "growth"];

/** The two cards, from GET /v1/plans. A plan the API did not describe still shows by name, so setup never stalls on
 *  a price list that could not be read. */
export function onboardingCards(plans: readonly PlanInfo[]): (PlanCard & { id: PlanId })[] {
  return PLAN_IDS.map((id) => {
    const p = plans.find((x) => x.id === id);
    return p ? { ...planCard(p), id } : { id, name: planLabel(id), price: null, features: [], note: null };
  });
}

/**
 * Sign-up's second step (plan v3): the company names itself and picks Free or Growth. Free goes on to Recall access;
 * Growth goes to Stripe Checkout first (test mode), and Stripe sends the company back to Billing. A full navigation
 * either way, so the console reads the new setup state.
 */
export function Onboarding({ plans, plansProblem = null, navigate }: { plans: readonly PlanInfo[]; plansProblem?: ApiProblem | null; navigate?: (url: string) => void }) {
  const [name, setName] = useState("");
  const [plan, setPlan] = useState<PlanId>("free");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [onFree, setOnFree] = useState(false);
  const cards = onboardingCards(plans);

  async function submit() {
    const body = onboardingBody(name, plan);
    if (!body) {
      setProblem({ status: 422, title: "Incomplete", detail: `Give your company's name, up to ${COMPANY_NAME_MAX} characters.`, correlationId: null, retryAfterSeconds: null });
      return;
    }
    setBusy(true);
    setProblem(null);
    const outcome = await submitOnboarding(body, navigate ?? ((url) => window.location.assign(url)));
    if (outcome.kind === "problem") {
      setProblem(outcome.problem);
      setBusy(false);
    } else if (outcome.kind === "on-free") {
      setOnFree(true);
    }
  }

  return (
    <div className="grid w-full max-w-3xl gap-6" data-onboarding>
      <header className="grid gap-2">
        <h1 className="page-title">Set up your company</h1>
        <p className="text-ink-muted">
          Name your company and pick a plan. Next, you ask for your recall, and the platform operator confirms that your company issued it.
        </p>
      </header>
      <form
        className="grid gap-6"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="grid max-w-md gap-1 text-sm">
          <label htmlFor="onboarding-company-name" className="font-medium text-ink">
            Company name
          </label>
          <input
            id="onboarding-company-name"
            aria-describedby="onboarding-company-name-help"
            name="company"
            autoComplete="organization"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={COMPANY_NAME_MAX}
            required
            disabled={busy || onFree}
            className="rounded border border-line-strong bg-surface px-3 py-2 text-base"
          />
          <p id="onboarding-company-name-help" className="text-xs text-ink-muted">
            The operator and your claimants see this name. You can change it under Billing until the operator approves your company.
          </p>
        </div>
        <fieldset className="grid gap-3">
          <legend className="section-title mb-3">Plan</legend>
          {plansProblem ? (
            <p className="text-sm text-warn-ink" data-plans-unavailable>
              Prices could not be loaded just now. Billing shows them once your company is set up.
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            {cards.map((card) => (
              <label
                key={card.id}
                className="grid cursor-pointer content-start gap-2 rounded-lg border border-line bg-surface p-4 has-[:checked]:border-accent has-[:checked]:bg-brand-soft"
                data-plan-card={card.id}
              >
                <span className="flex items-center gap-2">
                  <input type="radio" name="plan" value={card.id} checked={plan === card.id} onChange={() => setPlan(card.id)} disabled={busy || onFree} className="h-4 w-4 shrink-0" />
                  <span className="font-semibold text-ink">{card.name}</span>
                </span>
                {card.price ? <span className="text-sm font-medium text-ink">{card.price}</span> : null}
                {card.features.length ? (
                  <ul className="grid list-disc gap-1 pl-5 text-sm text-ink-muted">
                    {card.features.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                ) : null}
                {card.note ? <span className="text-xs text-ink-muted">{card.note}</span> : null}
              </label>
            ))}
          </div>
        </fieldset>
        {onFree ? null : (
          <button type="submit" disabled={busy} className="justify-self-start rounded-md bg-accent px-4 py-2 font-semibold text-bg hover:opacity-90 disabled:opacity-60" data-onboarding-submit>
            {busy ? "Saving…" : plan === "growth" ? "Continue to payment" : "Continue"}
          </button>
        )}
      </form>
      {problem ? <ProblemAlert problem={problem} /> : null}
      {onFree ? (
        <div role="status" className="grid gap-1 rounded-lg border border-brand/40 bg-brand-soft p-4 text-sm text-ink" data-onboarding-on-free>
          <p>Your company is set up on Free. Stripe checkout could not start here, so upgrade to Growth under Billing when you are ready.</p>
          <p>
            <a href={AFTER_ONBOARDING} className="font-medium underline">
              Continue to Recall access
            </a>
          </p>
        </div>
      ) : null}
    </div>
  );
}
