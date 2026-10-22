import type { Metadata } from "next";

import { CreateExport, ExportBatchTable, ExportSummary } from "@/components/PayoutExports";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadExports } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Payouts" };

export default async function PayoutsPage() {
  const role = await currentConsoleRole();
  const result = role === "admin" ? await loadExports() : null;
  return (
    <section aria-labelledby="payouts-title" className="grid grid-cols-1 gap-8">
      <header>
        <h1 id="payouts-title" className="page-title">
          Payouts
        </h1>
        <p className="mt-2 max-w-2xl text-ink-muted">
          After a refund is approved, the claimant chooses a bank transfer or a mailed check. Mailed checks leave
          RecallOps as an export file for your bank; bank transfers through Stripe are tracked on each claim.
        </p>
      </header>
      {role !== "admin" ? (
        <ProblemAlert problem={{ status: 403, title: "Forbidden", detail: null, correlationId: null, retryAfterSeconds: null }} sentence="This account cannot see or create payout export batches." />
      ) : !result!.ok ? (
        <ProblemAlert problem={result!.problem} />
      ) : (
        <>
          <ExportSummary items={result!.data.items} />
          <section aria-labelledby="new-batch" className="rounded-lg border border-line bg-surface p-4 sm:p-5">
            <h2 id="new-batch" className="section-title">
              New export batch
            </h2>
            <p className="mt-1 mb-4 text-sm text-ink-muted">
              Collects every approved refund waiting for a mailed check, across all products, into one CSV. A refund
              joins it when the claimant chose a check, or did not finish choosing or setting up a bank transfer by the
              deadline.
            </p>
            <CreateExport />
          </section>
          <section aria-labelledby="history" className="grid grid-cols-1 gap-3">
            <h2 id="history" className="section-title">
              Batch history
            </h2>
            <ExportBatchTable items={result!.data.items} />
          </section>
        </>
      )}
    </section>
  );
}
