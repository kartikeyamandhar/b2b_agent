import type { Metadata } from "next";

import { BillingScreen } from "@/components/BillingScreen";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Billing" };

/** Stripe returns to /app/billing/return (BILLING_RETURN_URL; the API refuses a query string in return URLs).
 *  /app/billing?stripe=return still works for links made before that page existed. */
export default async function BillingPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  return BillingScreen({ returned: sp.stripe === "return" });
}
