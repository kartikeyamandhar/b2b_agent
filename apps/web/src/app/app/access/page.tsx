import type { Metadata } from "next";

import { accessScreen } from "@/components/AccessGate";
import { loadAccess } from "@/lib/api/queries";
import { meOnce } from "@/lib/me";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Recall access" };

/** This company's requests to run a recall, their products, and how to ask for another. */
export default async function AccessPage() {
  const [access, me] = await Promise.all([loadAccess(), meOnce()]);
  const firm = me.ok ? me.data.firm : null;
  return accessScreen(access, firm, !!firm && !firm.approved);
}
