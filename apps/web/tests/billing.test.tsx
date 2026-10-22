// Billing and Usage. Every Stripe interaction here is mocked at the fetch boundary: no request leaves the test.
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  (globalThis as { window?: unknown }).window ??= globalThis;
});
vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import BillingPage from "@/app/app/billing/page";
import { BillingLive, usageQuery } from "@/components/BillingLive";
import { BillingView } from "@/components/BillingView";
import { Usage } from "@/lib/api-types";
import { startStripeSession } from "@/lib/billing-actions";
import { RETURN_POLL_MS, accessLimited, billingActions, meterRatio, meters, planLabel, planSummary } from "@/lib/billing";

// Plan v3 usage: the one limit is active products (the shared fixture still carries the old limit names).
const fixture = Usage.parse(JSON.parse(readFileSync(new URL("./fixtures/usage-free.json", import.meta.url), "utf8")));
const free = { ...fixture, active_products: 1, limits: { active_products: 1 } };
const growth = { ...free, plan: "growth", effective_plan: "growth", subscription_status: "active", limits: { active_products: null } };
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

  it("reads the active-product limit, with null as unlimited, and no claims or seat limits", () => {
    expect(meters(free).map((m) => [m.label, m.used, m.limit])).toEqual([["Active products", 1, 1]]);
    expect(meters(growth).map((m) => m.limit)).toEqual([null]);
    expect(meters({ ...free, active_products: undefined, open_programs: 2 })[0]!.used).toBe(2);
    expect(planSummary(free)).toMatch(/^Free covers one product at a time, with unlimited claims/);
    expect(planSummary(growth)).toMatch(/^Growth runs any number of products/);
    expect(planSummary({ effective_plan: "enterprise", limits: {} })).toBeNull();
    expect(meterRatio({ label: "x", used: 37, limit: 50 })).toBe(0.74);
    expect(meterRatio({ label: "x", used: 60, limit: 50 })).toBe(1);
    expect(meterRatio({ label: "x", used: 3, limit: null })).toBeNull();
  });
});

describe("BillingView and BillingLive", () => {
  it("shows the plan, the product meter, claims checked, and no invoices", () => {
    const out = renderToStaticMarkup(<BillingView usage={free} />);
    expect(out).toContain('data-plan="free"');
    expect(out).toContain("1 of 1");
    expect(out).toContain("Free covers one product at a time");
    expect(out).toMatch(/data-claims-checked="true">37</);
    expect(out).not.toMatch(/reviewer|seat|claims per month/i);
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
    expect(out).toContain("1 of unlimited");
  });

  it("offers Upgrade to Growth to the company on Free, and nothing to a development reviewer", () => {
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
    [403, "this account cannot manage billing"],
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
  const FIRM = { id: "00000000-0000-4000-8000-00000000d002", name: "Acme Kettles", onboarded: true, plan: "free", effective_plan: "free", subscription_status: null, approved: false, active_products: 0, product_limit: 1 };
  const PLANS = [
    { id: "free", product_limit: 1, program_price_cents: null, included_claims: null, claim_price_cents: null, currency: "usd", refund_options: ["stripe_connect", "check_export"] },
    { id: "growth", product_limit: null, program_price_cents: 4900, included_claims: 500, claim_price_cents: 25, currency: "usd", refund_options: ["stripe_connect", "check_export"] },
  ];

  beforeEach(() => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d002");
  });

  function serve(usage: unknown = free) {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      const path = new URL(url).pathname;
      if (path === "/v1/me") return Response.json({ operator: false, email: "ops@acme.example", firm: FIRM });
      if (path === "/v1/plans") return Response.json(PLANS);
      return Response.json(usage);
    });
    return urls;
  }

  const page = async (search: Record<string, string> = {}) =>
    render(await BillingPage({ searchParams: Promise.resolve(search) }));

  it("reads usage, the company, and the plans, and offers Checkout with Growth's price before approval", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    const urls = serve();
    const out = await page();
    expect([...urls].sort()).toEqual(["http://127.0.0.1:8000/v1/me", "http://127.0.0.1:8000/v1/plans", "http://127.0.0.1:8000/v1/usage"]);
    expect(out).toContain("Billing and usage");
    expect(out).toContain("data-billing-checkout");
    expect(out).toContain("Growth: $49.00 a month for each open product");
    expect(out).toContain("4242 4242 4242 4242");
    expect(out).toMatch(/data-company-name="true"[^>]*value="Acme Kettles"|value="Acme Kettles"[^>]*data-company-name="true"/);
  });

  it("is read-only for a development reviewer", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    serve();
    const out = await page();
    expect(out).toContain("data-billing-readonly");
    expect(out).toContain("This account cannot change the plan or payment details.");
    expect(out.replace(/<[^>]+>/g, " ")).not.toMatch(/organization/i); // visible text (autocomplete="organization" is the HTML token for a company name)
  });

  it("shows the return banner when Stripe sends the company back", async () => {
    serve();
    expect(await page({ stripe: "return" })).toContain("data-stripe-returned");
  });

  it("shows an error with its reference when usage cannot load", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ title: "x", status: 404, detail: "firm not found", correlation_id: "c-404" }, { status: 404 }));
    const out = await page();
    expect(out).toContain('role="alert"');
    expect(out).toContain("c-404");
  });
});
