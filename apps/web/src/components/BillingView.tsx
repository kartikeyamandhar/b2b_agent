import type { Usage } from "@/lib/api-types";
import { accessLimited, meterRatio, meters, planLabel } from "@/lib/billing";
import { formatCents, formatCount, formatDate, formatUsdCost } from "@/lib/format";

import { Panel } from "./ClaimPanels";

const SUB_LABEL: Record<string, string> = {
  active: "Active",
  trialing: "Trial",
  past_due: "Payment past due",
  unpaid: "Unpaid",
  canceled: "Canceled",
  incomplete: "Incomplete",
  incomplete_expired: "Expired",
  paused: "Paused",
};

/** Plan, Stripe status, usage against limits, and invoices (GET /v1/usage). Presentational. */
export function BillingView({ usage }: { usage: Usage }) {
  const limited = accessLimited(usage);
  const sub = usage.subscription_status;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel id="billing-plan" title="Plan" tone={limited || usage.billing_sync_error ? "attention" : undefined}>
        <dl className="grid gap-2 text-sm">
          <div className="flex flex-wrap items-baseline gap-2">
            <dt className="sr-only">Plan</dt>
            <dd className="kpi-value" data-plan={usage.plan}>
              {planLabel(usage.plan)}
            </dd>
            {sub ? (
              <dd className="rounded border border-line bg-surface px-1.5 py-0.5 text-xs" data-subscription={sub}>
                Stripe: {Object.hasOwn(SUB_LABEL, sub) ? SUB_LABEL[sub] : sub}
              </dd>
            ) : null}
          </div>
          {/* Access differs from the plan only while billing is unresolved; otherwise a line would repeat the plan name. */}
          {limited ? (
            <dd className="text-warn-ink" data-access-limited>
              Access is limited to {planLabel(usage.effective_plan)} features until billing is resolved, although the plan is{" "}
              {planLabel(usage.plan)}.
            </dd>
          ) : null}
          {usage.billing_sync_error ? (
            <dd className="text-warn-ink" data-sync-error>
              Billing sync needs attention: {usage.billing_sync_error}
            </dd>
          ) : null}
        </dl>
      </Panel>

      <Panel id="billing-usage" title={`Usage since ${formatDate(usage.period_start)}`}>
        <dl className="grid gap-3 text-sm">
          {meters(usage).map((m) => {
            const ratio = meterRatio(m);
            return (
              <div key={m.label} data-meter={m.label}>
                <div className="flex justify-between gap-2">
                  <dt>{m.label}</dt>
                  <dd className="tabular-nums">
                    {m.used === null ? "" : formatCount(m.used)}
                    {m.used === null ? "" : " of "}
                    {m.limit === null ? (m.used === null ? "Unlimited" : "unlimited") : formatCount(m.limit)}
                  </dd>
                </div>
                {ratio !== null ? (
                  <div className="mt-1 h-1.5 rounded bg-line" role="meter" aria-label={`${m.label} used`} aria-valuemin={0} aria-valuemax={1} aria-valuenow={ratio}>
                    <div className={`h-1.5 rounded ${ratio >= 1 ? "bg-warn" : "bg-brand"}`} style={{ width: `${Math.round(ratio * 100)}%` }} />
                  </div>
                ) : null}
              </div>
            );
          })}
          <div className="flex justify-between gap-2 text-ink-muted">
            <dt>Model spend this month</dt>
            <dd className="tabular-nums" data-model-spend>
              {formatUsdCost(usage.model_cost_usd)}
            </dd>
          </div>
        </dl>
      </Panel>

      <div className="lg:col-span-2">
        <Panel id="billing-invoices" title="Invoices">
          {usage.invoices.length === 0 ? (
            <p className="text-sm text-ink-muted">No invoices yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">Invoices</caption>
                <thead>
                  <tr className="border-b border-line text-left text-xs text-ink-muted">
                    <th scope="col" className="py-1 pr-3 font-medium">Invoice</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Status</th>
                    <th scope="col" className="py-1 pr-3 text-right font-medium">Due</th>
                    <th scope="col" className="py-1 pr-3 text-right font-medium">Paid</th>
                    <th scope="col" className="py-1 text-right font-medium">Attempts</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.invoices.map((i) => (
                    <tr key={i.id} className="border-b border-line last:border-b-0">
                      <td className="py-1 pr-3 font-mono text-xs">{i.id}</td>
                      <td className="py-1 pr-3">{i.status}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{i.currency.toLowerCase() === "usd" ? formatCents(i.amount_due_cents) : `${i.amount_due_cents} ${i.currency}`}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{i.currency.toLowerCase() === "usd" ? formatCents(i.amount_paid_cents) : `${i.amount_paid_cents} ${i.currency}`}</td>
                      <td className="py-1 text-right tabular-nums">{i.attempt_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
