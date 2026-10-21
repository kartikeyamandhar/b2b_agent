import { BillingLive } from "@/components/BillingLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadUsage } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";

/** The billing page; `returned` starts the short usage poll after Stripe sends the admin back. */
export async function BillingScreen({ returned }: { returned: boolean }) {
  const [usage, role] = await Promise.all([loadUsage(), currentConsoleRole()]);
  return (
    <div className="grid gap-4">
      <h1 className="page-title">Billing and usage</h1>
      {usage.ok ? (
        <BillingLive initialUsage={usage.data} canManage={role === "admin"} returned={returned} loadedAt={Date.now()} />
      ) : (
        <ProblemAlert problem={usage.problem} />
      )}
    </div>
  );
}
