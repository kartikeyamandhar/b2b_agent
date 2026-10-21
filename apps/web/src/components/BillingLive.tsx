"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Usage } from "@/lib/api-types";
import { fetchJson } from "@/lib/api/client";
import type { ApiProblem } from "@/lib/api/problem";
import { startStripeSession, type StripePage } from "@/lib/billing-actions";
import { RETURN_POLL_MS, RETURN_POLL_WINDOW_MS, billingActions } from "@/lib/billing";

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
export function BillingLive({ initialUsage, canManage, returned, loadedAt }: { initialUsage: Usage; canManage: boolean; returned: boolean; loadedAt: number }) {
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

  return (
    <div className="grid gap-4">
      {returned ? (
        <p role="status" className="rounded-lg border border-copper/40 border-l-4 border-l-copper bg-copper-soft px-4 py-3 text-sm" data-stripe-returned>
          Back from Stripe. The plan below changes only after Stripe confirms the payment to RecallOps
          {polling ? "; this page checks again every few seconds for a minute." : ". Refresh the page to check again."}
        </p>
      ) : null}
      <BillingView usage={usage} />
      <Panel id="billing-actions" title="Manage billing">
        {!canManage ? (
          <p className="text-sm text-ink-muted" data-billing-readonly>
            Only an organization admin can change the plan or payment details.
          </p>
        ) : actions.note ? (
          <p className="text-sm text-ink-muted">{actions.note}</p>
        ) : (
          <div className="grid gap-2">
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
              Checkout and subscription management open on Stripe&apos;s own pages (test mode). RecallOps never sees card
              details.
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
