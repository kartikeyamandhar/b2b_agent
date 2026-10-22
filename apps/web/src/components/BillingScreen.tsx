import { BillingLive, CompanyNameForm } from "@/components/BillingLive";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadUsage } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";
import { meOnce, plansOnce } from "@/lib/me";

/** The billing page, which a company reaches before the operator approves it (plan v3): its name, its plan, usage,
 *  and invoices. `returned` starts the short usage poll after Stripe sends the company back. */
export async function BillingScreen({ returned }: { returned: boolean }) {
  const [usage, role, me, plans] = await Promise.all([loadUsage(), currentConsoleRole(), meOnce(), plansOnce()]);
  const canManage = role === "admin";
  const firm = me.ok ? me.data.firm : null;
  const growth = plans.ok ? (plans.data.find((p) => p.id === "growth") ?? null) : null;
  return (
    <div className="grid grid-cols-1 gap-6">
      <header>
        <h1 className="page-title">Billing and usage</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">Your company&rsquo;s name, its plan, usage against the plan, and your invoices.</p>
      </header>
      {firm ? <CompanyNameForm initialName={firm.name} canManage={canManage} locked={firm.approved} /> : null}
      {usage.ok ? (
        <BillingLive initialUsage={usage.data} canManage={canManage} returned={returned} loadedAt={Date.now()} growth={growth} />
      ) : (
        <ProblemAlert problem={usage.problem} />
      )}
    </div>
  );
}
