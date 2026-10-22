// Billing page rules over GET /v1/usage (payments handoff: base access on effective_plan; the commercial plan and
// the Stripe status are separate; a null limit means unlimited). The backend decides; these only choose what to show.
// Plan v3: the one limit is active products (Free covers one at a time); claims and refund options are the same on
// every plan, and prices come from GET /v1/plans.
import type { OnboardingRequest, PlanInfo, Usage } from "@/lib/api-types";
import { formatCents, formatCount } from "@/lib/format";

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

/** The active-product limit from usage (limits.active_products; null or absent is unlimited). */
export function productLimit(u: Pick<Usage, "limits">): number | null {
  const v = Object.hasOwn(u.limits, "active_products") ? u.limits.active_products : null;
  return typeof v === "number" ? v : null;
}

/** The plan's one meter: products that are not closed, against the plan's limit. */
export function meters(u: Pick<Usage, "open_programs" | "active_products" | "limits">): Meter[] {
  return [{ label: "Active products", used: u.active_products ?? u.open_programs, limit: productLimit(u) }];
}

export function meterRatio(m: Meter): number | null {
  return m.used !== null && m.limit !== null && m.limit > 0 ? Math.min(m.used / m.limit, 1) : null;
}

/** "one product", "3 products". */
export function productCount(n: number): string {
  return n === 1 ? "one product" : `${formatCount(n)} products`;
}

/** What the AI agent does, said without a number: it decides up to a daily number of claims per product, and past
 *  that (or while the model budget is spent) the rules checker sorts claims and a person approves them. */
export const AGENT_LIMIT = "The AI agent checks claims, up to a daily limit per product";

/** One sentence on what the current plan covers, read from usage. */
export function planSummary(u: Pick<Usage, "effective_plan" | "limits">): string | null {
  if (u.effective_plan === "free") {
    const limit = productLimit(u);
    const covers = limit === null ? "any number of products" : `${productCount(limit)} at a time`;
    return `Free covers ${covers}, with unlimited claims and both refund options. ${AGENT_LIMIT}. Upgrade to Growth to run more products.`;
  }
  if (u.effective_plan === "growth") {
    return "Growth runs any number of products. Each open product is billed monthly, and claims past the included amount are billed per claim.";
  }
  return null;
}

/** Money in a plan's own currency: dollars for usd, otherwise the amount with its code. */
export function planMoney(cents: number, currency: string): string {
  return currency.toLowerCase() === "usd" ? formatCents(cents) : `${(cents / 100).toFixed(2)} ${currency.toUpperCase()}`;
}

export type PlanCard = { id: string; name: string; price: string | null; features: string[]; note: string | null };

export const TEST_CARD_NOTE = "Stripe runs in test mode: pay with the test card 4242 4242 4242 4242. No real money moves.";

/** The sign-up page's card for one plan, built only from GET /v1/plans. */
export function planCard(p: PlanInfo): PlanCard {
  const products = p.product_limit === null ? "Any number of products" : p.product_limit === 1 ? "One product at a time" : `${formatCount(p.product_limit)} products at a time`;
  const refunds =
    p.refund_options.includes("stripe_connect") && p.refund_options.includes("check_export")
      ? "Refunds by bank transfer or mailed check, as the claimant chooses"
      : p.refund_options.includes("check_export")
        ? "Refunds by mailed check"
        : "Refunds by bank transfer";
  if (p.id === "free") {
    return {
      id: p.id,
      name: planLabel(p.id),
      price: "No charge",
      features: [products, "Unlimited claims", AGENT_LIMIT, "A claim link and QR code for the product", "Owner notices", refunds],
      note: null,
    };
  }
  const claims =
    p.included_claims === null
      ? "Unlimited claims"
      : p.claim_price_cents === null
        ? `${formatCount(p.included_claims)} claims a month included`
        : `${formatCount(p.included_claims)} claims a month included, then ${planMoney(p.claim_price_cents, p.currency)} a claim`;
  return {
    id: p.id,
    name: planLabel(p.id),
    price: p.program_price_cents === null ? null : `${planMoney(p.program_price_cents, p.currency)} a month for each open product`,
    features: [products, claims, "Everything in Free", refunds],
    note: TEST_CARD_NOTE,
  };
}

export const COMPANY_NAME_MAX = 200;

/** The trimmed company name, or null when it is empty or too long (the API refuses both with a 422). */
export function companyName(raw: string): string | null {
  const name = raw.replace(/\s+/g, " ").trim();
  return name && name.length <= COMPANY_NAME_MAX ? name : null;
}

/** POST /v1/onboarding's body, or null while the name is missing. */
export function onboardingBody(rawName: string, plan: OnboardingRequest["plan"]): OnboardingRequest | null {
  const name = companyName(rawName);
  return name ? { name, plan } : null;
}

/** The page polls usage for a minute after returning from Stripe: only the webhook changes the plan. */
export const RETURN_POLL_MS = 3_000;
export const RETURN_POLL_WINDOW_MS = 60_000;
