import type { Metadata } from "next";
import Link from "next/link";

import { OperatorQueue } from "@/components/OperatorQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import { operatorQueueOnce } from "@/lib/api/operator";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Operator queue" };

/** The platform operator's queue of access requests (company verification). Others get a plain refusal. */
export default async function OperatorPage() {
  const list = await operatorQueueOnce();
  if (!list.ok) {
    return list.problem.status === 403 ? (
      <div className="grid max-w-2xl gap-3" data-operator-refused>
        <h1 className="page-title">Operator queue</h1>
        <p className="text-ink-muted">Only the platform operator can open this page.</p>
        <p>
          <Link href="/app/programs" className="text-accent hover:underline">
            Go to your console
          </Link>
        </p>
      </div>
    ) : (
      <ProblemAlert problem={list.problem} />
    );
  }
  return <OperatorQueue requests={list.data} />;
}
