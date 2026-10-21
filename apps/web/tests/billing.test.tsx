// Billing and Usage. Every Stripe interaction here is mocked at the fetch boundary: no request leaves the test.
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  (globalThis as { window?: unknown }).window ??= globalThis;
});

import BillingPage from "@/app/app/billing/page";
import { BillingLive, usageQuery } from "@/components/BillingLive";
import { BillingView } from "@/components/BillingView";
import { Usage } from "@/lib/api-types";
import { startStripeSession } from "@/lib/billing-actions";
import { RETURN_POLL_MS, accessLimited, billingActions, meterRatio, meters, planLabel } from "@/lib/billing";

const free = Usage.parse(JSON.parse(readFileSync(new URL("./fixtures/usage-free.json", import.meta.url), "utf8")));
const growth = { ...free, plan: "growth", effective_plan: "growth", subscription_status: "active", limits: { open_programs: null, claims_per_month: null, reviewer_seats: null } };
const render = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("billing rules", () => {
  it.each([
    [{ plan: "free", subscription_status: null }, { checkout: true, portal: false }],
    [{ plan: "free", subscription_status: "canceled" }, { checkout: true, portal: true }],
    [{ plan: "free", subscription_status: "incomplete" }, { checkout: false, portal: true }],
    [{ plan: "growth", subscription_status: "active" }, { checkout: false, portal: true }],
    [{ plan: "growth", subscription_status: null }, { checkout: false, portal: true }],
    [{ plan: "growth", subscription_status: "past_due" }, { checkout: false, portal: true }],
    [{ plan: "enterprise", subscription_status: null }, { checkout: false, portal: false }],
  ])("%j offers %j", (u, expected) => {
    expect(billingActions(u)).toMatchObject(expected);
  });

  it("explains enterprise, labels plans, and spots limited access", () => {
    expect(billingActions({ plan: "enterprise", subscription_status: null }).note).toMatch(/managed by RecallOps/);
    expect(planLabel("growth")).toBe("Growth");
    expect(planLabel("platinum")).toBe("platinum");
    expect(accessLimited({ plan: "growth", effective_plan: "free" })).toBe(true);
    expect(accessLimited({ plan: "free", effective_plan: "free" })).toBe(false);
  });

  it("reads limits, with null as unlimited", () => {
    expect(meters(free).map((m) => [m.used, m.limit])).toEqual([[1, 1], [37, 50], [null, 2]]);
    expect(meters(growth).map((m) => m.limit)).toEqual([null, null, null]);
    expect(meterRatio({ label: "x", used: 37, limit: 50 })).toBe(0.74);
    expect(meterRatio({ label: "x", used: 60, limit: 50 })).toBe(1);
    expect(meterRatio({ label: "x", used: 3, limit: null })).toBeNull();
  });
});

describe("BillingView and BillingLive", () => {
  it("shows the plan, meters against limits, and no invoices", () => {
    const out = renderToStaticMarkup(<BillingView usage={free} />);
    expect(out).toContain('data-plan="free"');
    expect(out).toContain("37 of 50");
    expect(out).toContain("Model spend this month");
    expect(out).toContain("No invoices yet.");
  });

  it("warns when paid-for access is limited and when billing sync failed, and lists invoices", () => {
    const out = renderToStaticMarkup(
      <BillingView usage={{ ...growth, effective_plan: "free", subscription_status: "unpaid", billing_sync_error: "quantity update failed", invoices: [{ id: "in_test_1", status: "open", amount_due_cents: 9900, amount_paid_cents: 0, attempt_count: 2, currency: "usd" }] }} />,
    );
    expect(out).toContain("data-access-limited");
    expect(out).toContain("Stripe: Unpaid");
    expect(out).toContain("Billing sync needs attention: quantity update failed");
    expect(out).toContain("in_test_1");
    expect(out).toContain("$99.00");
    expect(out).toContain("Unlimited");
  });

  it("offers Upgrade to Growth to an admin on Free, and nothing to a reviewer", () => {
    const admin = render(<BillingLive initialUsage={free} canManage returned={false} loadedAt={Date.now()} />);
    expect(admin).toContain("data-billing-checkout");
    expect(admin).not.toContain("data-billing-portal");
    expect(admin).toContain("RecallOps never sees card");
    const reviewer = render(<BillingLive initialUsage={free} canManage={false} returned={false} loadedAt={Date.now()} />);
    expect(reviewer).toContain("data-billing-readonly");
    expect(reviewer).not.toContain("data-billing-checkout");
  });

  it("offers the Customer Portal on Growth and explains Enterprise", () => {
    expect(render(<BillingLive initialUsage={growth} canManage returned={false} loadedAt={Date.now()} />)).toContain("data-billing-portal");
    const ent = render(<BillingLive initialUsage={{ ...free, plan: "enterprise", effective_plan: "enterprise" }} canManage returned={false} loadedAt={Date.now()} />);
    expect(ent).toContain("managed by RecallOps");
    expect(ent).not.toContain("data-billing-checkout");
  });

  it("says a return from Stripe is not payment until Stripe confirms", () => {
    const out = render(<BillingLive initialUsage={free} canManage returned loadedAt={Date.now()} />);
    expect(out).toContain("data-stripe-returned");
    expect(out).toContain("only after Stripe confirms");
  });
});

describe("startStripeSession (mocked API, no Stripe calls)", () => {
  function stub(res: () => Response) {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return res();
    });
    return seen;
  }

  it("asks our API for a Checkout session and navigates to the Stripe page it returns", async () => {
    const seen = stub(() => Response.json({ url: "https://checkout.stripe.com/c/pay/cs_test_mock" }));
    const navigate = vi.fn();
    expect(await startStripeSession("checkout", navigate)).toBeNull();
    expect(seen[0]!.url).toBe("/api/v1/billing/checkout");
    expect(seen[0]!.init.method).toBe("POST");
    expect(seen[0]!.init.body).toBeUndefined();
    expect(navigate).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_mock");
  });

  it("opens the Customer Portal the same way", async () => {
    const seen = stub(() => Response.json({ url: "https://billing.stripe.com/p/session/test_mock" }));
    const navigate = vi.fn();
    await startStripeSession("portal", navigate);
    expect(seen[0]!.url).toBe("/api/v1/billing/portal");
    expect(navigate).toHaveBeenCalledWith("https://billing.stripe.com/p/session/test_mock");
  });

  it("never navigates anywhere but Stripe", async () => {
    for (const url of ["https://evil.example/checkout", "http://checkout.stripe.com/x", "javascript:alert(1)", "https://stripe.com.evil.example/"]) {
      stub(() => Response.json({ url }));
      const navigate = vi.fn();
      expect((await startStripeSession("checkout", navigate))?.status).toBe(502);
      expect(navigate).not.toHaveBeenCalled();
    }
  });

  it.each([
    [403, "the reviewer role cannot manage billing"],
    [409, "a subscription already exists; use Customer Portal"],
    [503, "Stripe test prices are not configured"],
  ])("returns the API's %i problem without navigating", async (status, detail) => {
    stub(() => Response.json({ title: "x", status, detail, correlation_id: `c-${status}` }, { status }));
    const navigate = vi.fn();
    expect(await startStripeSession("checkout", navigate)).toMatchObject({ status, detail, correlationId: `c-${status}` });
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("usage polling after a Stripe return", () => {
  it("re-reads usage every 3 s for the window, then stops", async () => {
    vi.useFakeTimers();
    let calls = 0;
    vi.stubGlobal("fetch", async () => {
      calls += 1;
      return Response.json(free);
    });
    const client = new QueryClient();
    client.mount();
    const off = new QueryObserver(client, usageQuery(Date.now() + 9_000) as never).subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 3);
    const during = calls;
    expect(during).toBeGreaterThanOrEqual(3);
    await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 10);
    expect(calls).toBeLessThanOrEqual(during + 1);
    off();
    client.unmount();
  });
});

describe("/app/billing page", () => {
  beforeEach(() => {
    vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d002");
  });

  const page = async (search: Record<string, string> = {}) =>
    render(await BillingPage({ searchParams: Promise.resolve(search) }));

  it("reads usage and offers Checkout to an admin", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => (urls.push(url), Response.json(free)));
    const out = await page();
    expect(urls).toEqual(["http://api.test/v1/usage"]);
    expect(out).toContain("Billing and usage");
    expect(out).toContain("data-billing-checkout");
  });

  it("is read-only for a reviewer", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    vi.stubGlobal("fetch", async () => Response.json(free));
    expect(await page()).toContain("data-billing-readonly");
  });

  it("shows the return banner when Stripe sends the admin back", async () => {
    vi.stubGlobal("fetch", async () => Response.json(free));
    expect(await page({ stripe: "return" })).toContain("data-stripe-returned");
  });

  it("shows an error with its reference when usage cannot load", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ title: "x", status: 404, detail: "firm not found", correlation_id: "c-404" }, { status: 404 }));
    const out = await page();
    expect(out).toContain('role="alert"');
    expect(out).toContain("c-404");
  });
});
