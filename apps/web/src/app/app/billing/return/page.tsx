import type { Metadata } from "next";

import { BillingScreen } from "@/components/BillingScreen";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Billing" };

/** BILLING_RETURN_URL: where Stripe Checkout and the Customer Portal send the admin back. */
export default async function BillingReturnPage() {
  return BillingScreen({ returned: true });
}
