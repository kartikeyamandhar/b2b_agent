import type { Metadata } from "next";

import { BillingLive } from "@/components/BillingLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadUsage } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Billing" };

/** Stripe returns here (BILLING_RETURN_URL should be /app/billing?stripe=return). */
export default async function BillingPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const [usage, role] = await Promise.all([loadUsage(), currentConsoleRole()]);
  return (
    <div className="grid gap-4">
      <h1 className="page-title">Billing and usage</h1>
      {usage.ok ? (
        <BillingLive initialUsage={usage.data} canManage={role === "admin"} returned={sp.stripe === "return"} loadedAt={Date.now()} />
      ) : (
        <ProblemAlert problem={usage.problem} />
      )}
    </div>
  );
}
