"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { PortalPayout, PortalView } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { isStripeOnboardingUrl, methodAfterPayoutError, portalPath, rememberConnectClaim } from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

import { ProblemAlert } from "../ProblemAlert";

/**
 * The refund destination, offered only after approval while the payout can still change (portal.py CHOOSABLE).
 * Stripe onboarding happens on Stripe's own page; a redirect back never means the money arrived, so the status
 * page always re-reads the API.
 */
export function PayoutChoice({ token, current }: { token: string; current: string | null }) {
  const router = useRouter();
  const [method, setMethod] = useState<"check_export" | "stripe_connect">(current === "onboarding" ? "stripe_connect" : "check_export");
  const [address, setAddress] = useState("");
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);

  async function choose() {
    const body = PortalPayout.safeParse({ method, mailing_address: method === "check_export" && address.trim() ? address.trim() : null });
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
          rememberConnectClaim(window.sessionStorage, token);
          window.location.assign(view.connect_onboarding_url);
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
      className="grid gap-3 rounded border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void choose();
      }}
    >
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
      {method === "check_export" ? (
        <div className="grid gap-1">
          <label htmlFor="mailing" className="text-base font-medium">
            Mailing address <span className="font-normal text-ink-muted">(optional)</span>
          </label>
          <p id="mailing-hint" className="text-sm text-ink-muted">
            Leave this empty to mail the check to the home address on your claim.
          </p>
          <textarea id="mailing" aria-describedby="mailing-hint" value={address} onChange={(e) => setAddress(e.target.value)} rows={3} maxLength={300} className="rounded border border-line-strong bg-bg p-3 text-base" />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">You leave this site to finish on Stripe, then come back here.</p>
      )}
      {problem ? <ProblemAlert problem={problem} sentence={portalSentence(problem.status)} /> : null}
      <button type="submit" disabled={busy} className="min-h-12 rounded bg-accent px-4 text-base font-semibold text-bg disabled:opacity-60" data-payout-submit>
        {busy ? "Saving…" : method === "stripe_connect" ? (current === "onboarding" ? "Continue on Stripe" : "Set up on Stripe") : current === "pending_export" ? "Update mailing address" : "Choose a mailed check"}
      </button>
    </form>
  );
}
