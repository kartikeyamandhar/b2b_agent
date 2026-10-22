import type { Metadata } from "next";

import { AccessRequests } from "@/components/AccessRequests";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadAccess } from "@/lib/api/queries";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Recall access" };

/** This organization's requests to run a recall, and how to ask for another. */
export default async function AccessPage() {
  const access = await loadAccess();
  return access.ok ? <AccessRequests status={access.data} /> : <ProblemAlert problem={access.problem} />;
}
