import type { Metadata } from "next";

import { OperatorQueue } from "@/components/OperatorQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadOperatorAccess } from "@/lib/api/queries";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Operator" };

/** The platform operator's queue of access requests (company verification). Others get a plain refusal. */
export default async function OperatorPage() {
  const list = await loadOperatorAccess();
  if (!list.ok) {
    return list.problem.status === 403 ? (
      <p className="mx-auto mt-10 max-w-md text-ink-muted">Only the platform operator can open this page.</p>
    ) : (
      <ProblemAlert problem={list.problem} />
    );
  }
  return <OperatorQueue requests={list.data} />;
}
