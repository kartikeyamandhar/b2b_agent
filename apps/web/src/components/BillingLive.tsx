"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { MeFirm, Usage, type PlanInfo } from "@/lib/api-types";
import { fetchJson, sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { startStripeSession, type StripePage } from "@/lib/billing-actions";
import { COMPANY_NAME_MAX, RETURN_POLL_MS, RETURN_POLL_WINDOW_MS, billingActions, companyName, planCard } from "@/lib/billing";

import { BillingView } from "./BillingView";
import { Panel } from "./ClaimPanels";
import { ProblemAlert } from "./ProblemAlert";

export function usageQuery(pollUntil: number) {
  return {
    queryKey: ["usage"] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchJson(Usage, "/v1/usage", "", signal),
    // Back from Stripe: the plan changes only when Stripe's webhook reaches the API, so re-read for a minute.
    refetchInterval: () => (Date.now() < pollUntil ? RETURN_POLL_MS : false),
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 1_000,
  };
}

/**
 * Billing actions only ask the API for a Stripe-hosted page and send the browser there. No card or bank details
 * pass through RecallOps, and a redirect back never counts as payment: the plan shown is the API's.
 */
export function BillingLive({
  initialUsage,
  canManage,
  returned,
  loadedAt,
  growth = null,
}: {
  initialUsage: Usage;
  canManage: boolean;
  returned: boolean;
  loadedAt: number;
  /** Growth as GET /v1/plans describes it, shown beside the upgrade button (null when the plans could not be read). */
  growth?: PlanInfo | null;
}) {
  const [pollUntil] = useState(() => (returned ? Date.now() + RETURN_POLL_WINDOW_MS : 0));
  const q = useQuery({ ...usageQuery(pollUntil), initialData: initialUsage, initialDataUpdatedAt: returned ? 0 : loadedAt });
  const usage = q.data;
  const actions = billingActions(usage);
  const [busy, setBusy] = useState<StripePage | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function go(kind: StripePage) {
    setBusy(kind);
    setProblem(null);
    const p = await startStripeSession(kind, (url) => window.location.assign(url));
    if (p) {
      setProblem(p);
      setBusy(null);
    }
  }

  const polling = returned && Date.now() < pollUntil;
  const growthCard = growth ? planCard(growth) : null;

  return (
    <div className="grid gap-4">
      {returned ? (
        <p role="status" className="rounded-lg border border-brand/40 border-l-4 border-l-brand bg-brand-soft px-4 py-3 text-sm" data-stripe-returned>
          Back from Stripe. The plan below changes only after Stripe confirms the payment to RecallOps
          {polling ? "; this page checks again every few seconds for a minute." : ". Refresh the page to check again."}
        </p>
      ) : null}
      <BillingView usage={usage} />
      <Panel id="billing-actions" title="Manage billing">
        {!canManage ? (
          <p className="text-sm text-ink-muted" data-billing-readonly>
            This account cannot change the plan or payment details.
          </p>
        ) : actions.note ? (
          <p className="text-sm text-ink-muted">{actions.note}</p>
        ) : (
          <div className="grid gap-2">
            {actions.checkout && growthCard ? (
              <div className="grid gap-1 text-sm" data-growth-offer>
                <p className="font-medium text-ink">Growth{growthCard.price ? `: ${growthCard.price}` : ""}</p>
                <p className="text-ink-muted">{growthCard.features.slice(0, 2).join(". ")}.</p>
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {actions.checkout ? (
                <button type="button" onClick={() => void go("checkout")} disabled={busy !== null} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-60 hover:opacity-90" data-billing-checkout>
                  {busy === "checkout" ? "Opening Stripe…" : "Upgrade to Growth"}
                </button>
              ) : null}
              {actions.portal ? (
                <button type="button" onClick={() => void go("portal")} disabled={busy !== null} className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink disabled:opacity-60 hover:border-ink" data-billing-portal>
                  {busy === "portal" ? "Opening Stripe…" : "Manage subscription"}
                </button>
              ) : null}
            </div>
            <p className="text-xs text-ink-muted">
              Checkout and subscription management open on Stripe&apos;s own pages in test mode: pay with the test card 4242 4242 4242 4242.
              RecallOps never sees card details.
            </p>
          </div>
        )}
        {problem ? (
          <div className="mt-2">
            <ProblemAlert problem={problem} />
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

/** The company's name (PATCH /v1/firm): what the operator and claimants see. The sidebar updates after a save. */
/** `locked`: the operator approved the company under this name, which claimants read on every claim page, so only the
 *  operator renames it now (the API answers 409). */
export function CompanyNameForm({ initialName, canManage, locked = false }: { initialName: string; canManage: boolean; locked?: boolean }) {
  const router = useRouter();
  const [name, setName] = useState(initialName);
  const [saved, setSaved] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const clean = companyName(name);

  async function save() {
    if (!clean) {
      setProblem({ status: 422, title: "Incomplete", detail: `Give your company's name, up to ${COMPANY_NAME_MAX} characters.`, correlationId: null, retryAfterSeconds: null });
      return;
    }
    setBusy(true);
    setProblem(null);
    setDone(false);
    try {
      const firm = await sendJson(MeFirm, "PATCH", "/v1/firm", { name: clean });
      setName(firm.name);
      setSaved(firm.name);
      setDone(true);
      router.refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel id="billing-company" title="Company">
      <form
        className="grid gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="grid gap-1 text-sm">
          <label htmlFor="billing-company-name" className="font-medium text-ink">
            Company name
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="billing-company-name"
              aria-describedby="billing-company-name-help"
              name="company"
              autoComplete="organization"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setDone(false);
              }}
              maxLength={COMPANY_NAME_MAX}
              required
              disabled={!canManage || locked || busy}
              className="min-w-0 rounded border border-line-strong bg-surface px-3 py-2 text-base sm:flex-1"
              data-company-name
            />
            {canManage && !locked ? (
              <button type="submit" disabled={busy || clean === saved} className="shrink-0 rounded-md border border-line-strong bg-surface px-4 py-2 font-medium text-ink hover:border-ink disabled:opacity-60">
                {busy ? "Saving…" : "Save"}
              </button>
            ) : null}
          </div>
        </div>
        <p id="billing-company-name-help" className="text-xs text-ink-muted">
          {locked
            ? "The operator approved your company under this name, and your claimants see it. To change it, ask the operator."
            : "The operator and your claimants see this name. You can change it until the operator approves your company."}
        </p>
        {done ? (
          <p role="status" className="text-sm text-success-ink" data-company-saved>
            Saved.
          </p>
        ) : null}
        {problem ? <ProblemAlert problem={problem} /> : null}
      </form>
    </Panel>
  );
}
