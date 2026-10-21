"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { takeConnectClaim } from "@/lib/portal";

/**
 * Landing page for Stripe's return and refresh URLs, which are one fixed setting for every claim. The tab remembered
 * which claim went to Stripe; its status page then re-reads the API, because a return alone proves nothing.
 */
export function ConnectReturn({ kind }: { kind: "return" | "refresh" }) {
  const router = useRouter();
  const [lost, setLost] = useState(false);
  useEffect(() => {
    let store: Storage | null = null;
    try {
      store = window.sessionStorage;
    } catch {
      store = null;
    }
    const token = takeConnectClaim(store);
    if (token) router.replace(`/claim/${encodeURIComponent(token)}/status`);
    else setLost(true);
  }, [router]);
  return lost ? (
    <p className="text-base">
      {kind === "refresh" ? "Your Stripe setup link expired. " : "You are back from Stripe. "}
      Open the claim link the firm sent you to see your refund status{kind === "refresh" ? " and get a new setup link" : ""}.
    </p>
  ) : (
    <p role="status" className="text-base">
      Taking you back to your claim…
    </p>
  );
}
