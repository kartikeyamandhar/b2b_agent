// Billing page rules over GET /v1/usage (payments handoff: base access on effective_plan; the commercial plan and
// the Stripe status are separate; a null limit means unlimited). The backend decides; these only choose what to show.
import type { Usage } from "@/lib/api-types";

const PLAN_LABEL: Record<string, string> = { free: "Free", growth: "Growth", enterprise: "Enterprise" };

export function planLabel(plan: string | null | undefined): string {
  if (!plan) return "Unknown";
  return Object.hasOwn(PLAN_LABEL, plan) ? PLAN_LABEL[plan]! : plan;
}

/** Stripe subscription states after which a new Checkout is allowed again (billing/service.py). */
const ENDED = ["canceled", "incomplete_expired"];

export type BillingActions = {
  checkout: boolean;
  portal: boolean;
  /** Why neither applies, in plain words. */
  note: string | null;
};

export function billingActions(u: Pick<Usage, "plan" | "subscription_status">): BillingActions {
  if (u.plan === "enterprise") {
    return { checkout: false, portal: false, note: "Enterprise subscriptions are managed by RecallOps directly." };
  }
  const liveSubscription = !!u.subscription_status && !ENDED.includes(u.subscription_status);
  return {
    checkout: u.plan === "free" && !liveSubscription,
    portal: u.plan === "growth" || !!u.subscription_status,
    note: null,
  };
}

/** The firm pays for Growth but has Free access (for example after a second failed collection). */
export function accessLimited(u: Pick<Usage, "plan" | "effective_plan">): boolean {
  return u.plan !== u.effective_plan;
}

export type Meter = { label: string; used: number | null; limit: number | null };

export function meters(u: Pick<Usage, "open_programs" | "claims_adjudicated" | "limits">): Meter[] {
  const limit = (k: string): number | null => {
    const v = Object.hasOwn(u.limits, k) ? u.limits[k] : null;
    return typeof v === "number" ? v : null;
  };
  return [
    { label: "Open programs", used: u.open_programs, limit: limit("open_programs") },
    { label: "Claims adjudicated this month", used: u.claims_adjudicated, limit: limit("claims_per_month") },
    { label: "Reviewer seats", used: null, limit: limit("reviewer_seats") },
  ];
}

export function meterRatio(m: Meter): number | null {
  return m.used !== null && m.limit !== null && m.limit > 0 ? Math.min(m.used / m.limit, 1) : null;
}

/** The page polls usage for a minute after returning from Stripe: only the webhook changes the plan. */
export const RETURN_POLL_MS = 3_000;
export const RETURN_POLL_WINDOW_MS = 60_000;
