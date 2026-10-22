"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { PortalPayout, PortalView } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { handOffToStripe, isStripeOnboardingUrl, methodAfterPayoutError, offersBankTransfer, payoutSentence, portalPath } from "@/lib/portal";

import { ProblemAlert } from "../ProblemAlert";

/**
 * The refund destination, offered only after approval while the payout can still change (portal.py CHOOSABLE).
 * Stripe onboarding happens on Stripe's own page; a redirect back never means the money arrived, so the status
 * page always re-reads the API. A scheduled check (pending_export) can only change its mailing address: the API
 * refuses Stripe onboarding from that state, so the bank transfer is not offered there.
 */
export function PayoutChoice({ token, current }: { token: string; current: string | null }) {
  const router = useRouter();
  const bankTransfer = offersBankTransfer(current);
  const [method, setMethod] = useState<"check_export" | "stripe_connect">(bankTransfer && current === "onboarding" ? "stripe_connect" : "check_export");
  const [address, setAddress] = useState("");
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const checkScheduled = current === "pending_export";

  async function choose() {
    // A scheduled check with no new address would change nothing, so the button waits for one.
    if (checkScheduled && !address.trim()) return;
    const body = PortalPayout.safeParse({ method: bankTransfer ? method : "check_export", mailing_address: method === "check_export" && address.trim() ? address.trim() : null });
    if (!body.success) {
      setProblem({ status: 422, title: "Invalid", detail: "The mailing address must be at most 300 characters.", correlationId: null, retryAfterSeconds: null });
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const view = await sendJson(PortalView, "POST", portalPath(token, "payout"), body.data);
      if (method === "stripe_connect") {
        if (isStripeOnboardingUrl(view.connect_onboarding_url)) {
          handOffToStripe(view.connect_onboarding_url, token, (url) => window.location.assign(url));
          return;
        }
        setProblem({ status: 503, title: "Unavailable", detail: "Stripe did not return a setup link.", correlationId: null, retryAfterSeconds: null });
      }
      router.refresh();
    } catch (err) {
      const p = err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
      // 402: this firm's plan pays refunds by mailed check only (portal.py), so offer the check instead.
      setMethod((m) => methodAfterPayoutError(p.status, m));
      setProblem(p);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="grid gap-3 rounded-lg border border-line bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void choose();
      }}
    >
      {bankTransfer ? (
        <fieldset className="grid gap-2">
          <legend className="text-base font-medium">How do you want to receive your refund?</legend>
          <label className="flex min-h-12 items-center gap-3 rounded border border-line-strong px-3 text-base">
            <input type="radio" name="method" checked={method === "check_export"} onChange={() => setMethod("check_export")} />
            A mailed check
          </label>
          <label className="flex min-h-12 items-center gap-3 rounded border border-line-strong px-3 text-base">
            <input type="radio" name="method" checked={method === "stripe_connect"} onChange={() => setMethod("stripe_connect")} />
            A bank transfer (set up on Stripe&apos;s secure page)
          </label>
        </fieldset>
      ) : (
        <p className="text-base" data-check-scheduled>
          A check is already scheduled for this refund, so a bank transfer is not available. If the check should go to a different address, enter it below.
        </p>
      )}
      {method === "check_export" ? (
        <div className="grid gap-1">
          <label htmlFor="mailing" className="text-base font-medium">
            {checkScheduled ? (
              "New mailing address"
            ) : (
              <>
                Mailing address <span className="font-normal text-ink-muted">(optional)</span>
              </>
            )}
          </label>
          <p id="mailing-hint" className="text-sm text-ink-muted">
            {checkScheduled ? "Until you save a new one, the check goes to the address already on your claim." : "Leave this empty to mail the check to the home address on your claim."}
          </p>
          <textarea id="mailing" aria-describedby="mailing-hint" value={address} onChange={(e) => setAddress(e.target.value)} rows={3} maxLength={300} className="rounded border border-line-strong bg-surface p-3 text-base" />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">You leave this site to finish on Stripe, then come back here.</p>
      )}
      {problem ? <ProblemAlert problem={problem} sentence={payoutSentence(problem.status)} audience="claimant" /> : null}
      <button type="submit" disabled={busy || (checkScheduled && !address.trim())} className="min-h-12 rounded-md bg-accent px-4 text-base font-semibold text-bg hover:opacity-90 disabled:opacity-60" data-payout-submit>
        {busy ? "Saving…" : method === "stripe_connect" ? (current === "onboarding" ? "Continue on Stripe" : "Set up on Stripe") : current === "pending_export" ? "Update mailing address" : "Choose a mailed check"}
      </button>
    </form>
  );
}
