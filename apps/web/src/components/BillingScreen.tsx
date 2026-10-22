import { BillingLive } from "@/components/BillingLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadUsage } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

/** The billing page; `returned` starts the short usage poll after Stripe sends the admin back. */
export async function BillingScreen({ returned }: { returned: boolean }) {
  const [usage, role] = await Promise.all([loadUsage(), currentConsoleRole()]);
  return (
    <div className="grid grid-cols-1 gap-6">
      <header>
        <h1 className="page-title">Billing and usage</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">Your plan, this month&rsquo;s usage against its limits, and your invoices.</p>
      </header>
      {usage.ok ? (
        <BillingLive initialUsage={usage.data} canManage={role === "admin"} returned={returned} loadedAt={Date.now()} />
      ) : (
        <ProblemAlert problem={usage.problem} />
      )}
    </div>
  );
}
