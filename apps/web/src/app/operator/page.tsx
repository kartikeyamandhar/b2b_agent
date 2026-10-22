import type { Metadata } from "next";

import { OperatorQueueLive } from "@/components/OperatorQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import { operatorQueueOnce } from "@/lib/api/operator";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Operator" };

/** Companies' requests to run a recall, for the platform operator to approve or reject. */
export default async function OperatorPage() {
  const list = await operatorQueueOnce();
  return list.ok ? <OperatorQueueLive initialRequests={list.data} loadedAt={Date.now()} /> : <ProblemAlert problem={list.problem} />;
}
