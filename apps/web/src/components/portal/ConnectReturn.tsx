"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { claimPagePath, sessionStore, takeConnectClaim } from "@/lib/portal";

/**
 * Landing page for Stripe's return and refresh URLs, which are one fixed setting for every claim. Only the tab that
 * went to Stripe knows its claim; any other tab asks the claimant to open their private link, never guessing a claim
 * this device saw before (on a shared device it could be someone else's). The status page then re-reads the API,
 * because a return alone proves nothing.
 */
export function ConnectReturn({ kind }: { kind: "return" | "refresh" }) {
  const router = useRouter();
  const [lost, setLost] = useState(false);
  useEffect(() => {
    const token = takeConnectClaim(sessionStore());
    if (token) router.replace(`${claimPagePath(token)}/status`);
    else setLost(true);
  }, [router]);
  return <ConnectReturnCard kind={kind} lost={lost} />;
}

/** The same card as the status page, with one heading for both moments, so the page does not jump when the tab
 *  turns out to have lost the claim. */
export function ConnectReturnCard({ kind, lost }: { kind: "return" | "refresh"; lost: boolean }) {
  return (
    <section aria-labelledby="connect-title" className="rounded-xl border border-line bg-surface p-5">
      <h1 id="connect-title" className="display-title text-2xl">
        {kind === "refresh" ? "Your Stripe setup link expired" : "You are back from Stripe"}
      </h1>
      {lost ? (
        <p className="mt-1 text-base" data-connect-lost>
          Open your private claim link to {kind === "refresh" ? "get a new setup link and see your refund status" : "see your refund status"}.
        </p>
      ) : (
        <p role="status" className="mt-1 text-base">
          Taking you back to your claim…
        </p>
      )}
    </section>
  );
}
