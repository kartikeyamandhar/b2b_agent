// Plan v3 accounts (owner decisions 2026-10-22): one account is one company, the operator has a window of their own
// and no company, plans are chosen at setup, and approving a request opens the company's product at once. Every API
// call is mocked at the fetch boundary, and Clerk is mocked: no test reaches the network.
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => {
  (globalThis as { window?: unknown }).window ??= globalThis;
  return { path: "/app/programs" };
});

vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => <div data-clerk-provider>{children}</div>,
  UserButton: () => <div data-user-button />,
  SignOutButton: ({ children, redirectUrl }: { children: ReactNode; redirectUrl?: string }) => <div data-sign-out-to={redirectUrl}>{children}</div>,
  SignIn: (p: { fallbackRedirectUrl?: string }) => <div data-sign-in-to={p.fallbackRedirectUrl} />,
  SignUp: (p: { fallbackRedirectUrl?: string }) => <div data-sign-up-to={p.fallbackRedirectUrl} />,
}));
vi.mock("next/navigation", async (orig) => ({
  ...(await orig<typeof import("next/navigation")>()),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => nav.path,
}));

import Home from "@/app/page";
import ConsoleLayout from "@/app/app/layout";
import ConsoleHome from "@/app/app/page";
import OldOperatorPage from "@/app/app/operator/page";
import WelcomePage from "@/app/app/welcome/page";
import { Onboarding, onboardingCards } from "@/app/app/welcome/Onboarding";
import OperatorLayout from "@/app/operator/layout";
import OperatorPage from "@/app/operator/page";
import OrganizationPage from "@/app/organization/page";
import SignInPage from "@/app/sign-in/[[...sign-in]]/page";
import SignUpPage from "@/app/sign-up/[[...sign-up]]/page";
import { GATED_NAV } from "@/components/AccessGate";
import { AccessProblem, AccessRequests } from "@/components/AccessRequests";
import { AccountControls, SignOutControl } from "@/components/AccountControls";
import { CONSOLE_NAV } from "@/components/AppShell";
import { PathGate, currentHref } from "@/components/NavLinks";
import { OperatorQueue, operatorQuery } from "@/components/OperatorQueue";
import type { AccessRequest, AccessStatus, Me, MeFirm, OperatorAccessRequest, PlanInfo } from "@/lib/api-types";
import {
  OPERATOR_POLL_MS,
  REVOKE_CONFIRM,
  approvalOutcome,
  decisionOutcome,
  emailWarnings,
  newestFirst,
  planIsFull,
  productState,
  programBody,
  recallLabel,
} from "@/lib/access";
import { humanSentence, isNotOnboarded, isSignInUnavailable } from "@/lib/api/problem";
import { AFTER_ONBOARDING, submitOnboarding } from "@/lib/billing-actions";
import { TEST_CARD_NOTE, companyName, onboardingBody, planCard, planMoney, productCount } from "@/lib/billing";
import { accountHome, companyIdentity, consoleStart } from "@/lib/me";

const API = "http://127.0.0.1:8000";
const DEV = { RECALLOPS_API_URL: API, RECALLOPS_WEB_AUTH: "dev", RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001", RECALLOPS_DEV_ROLE: "admin" };
const CLERK = { RECALLOPS_API_URL: API, RECALLOPS_WEB_AUTH: "clerk", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_placeholder", CLERK_SECRET_KEY: "sk_test_placeholder" };

const FIRM: MeFirm = {
  id: "00000000-0000-4000-8000-00000000d001",
  name: "Acme Kettles",
  onboarded: true,
  plan: "free",
  effective_plan: "free",
  subscription_status: null,
  approved: true,
  active_products: 1,
  product_limit: 1,
};
const COMPANY: Me = { operator: false, email: "ops@acme.example", firm: FIRM };
const OPERATOR: Me = { operator: true, email: "kartikeyamandhar777@gmail.com", firm: null };
const NEW_COMPANY: Me = { operator: false, email: "new@acme.example", firm: { ...FIRM, name: "New company", onboarded: false, approved: false, active_products: 0 } };
const UNAPPROVED: Me = { operator: false, email: "ops@acme.example", firm: { ...FIRM, approved: false, active_products: 0 } };
const FREE: PlanInfo = { id: "free", product_limit: 1, program_price_cents: null, included_claims: null, claim_price_cents: null, currency: "usd", refund_options: ["stripe_connect", "check_export"] };
const GROWTH: PlanInfo = { id: "growth", product_limit: null, program_price_cents: 4900, included_claims: 500, claim_price_cents: 25, currency: "usd", refund_options: ["stripe_connect", "check_export"] };
const REQ: AccessRequest = { id: "a1", recall_id: "r1", title: "Acme Kettle Recall", typed: false, status: "approved", created_at: "2026-10-22T00:10:00Z", decided_at: "2026-10-22T01:00:00Z" };
const OP: OperatorAccessRequest = {
  id: "o1",
  firm_id: "f1",
  firm_name: "Acme Kettles",
  recall_id: "r1",
  recall_number: "26999",
  title: "Acme Recalls Kettles",
  typed: false,
  typed_company: null,
  status: "requested",
  requested_by: "user_x",
  requester_domain: "acme.example",
  requester_email: "ops@acme.example",
  requester_email_verified: true,
  requester_test_address: false,
  firm_plan: "free",
  firm_active_products: 0,
  notice_company: "Acme",
  notice_sites: [],
  domain_matches: true,
  approved_elsewhere: null,
  decided_by: null,
  decided_at: null,
  note: null,
  created_at: "2026-10-22T00:10:00Z",
};
const status = (s: Partial<AccessStatus>): AccessStatus => ({ required: true, approved: false, operator: false, requests: [], ...s });
const digestOf = (err: unknown) => String((err as { digest?: string }).digest);
const problem503 = { title: "Service Unavailable", status: 503, detail: "sign-in verification unavailable", correlation_id: "c-503" };

/** A fake API for the server components: GET /v1/me answers `me`, and the rest answer what each test needs. */
function serve(me: Me | "unavailable", extra: Record<string, unknown> = {}) {
  const urls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    const path = new URL(url).pathname;
    if (path === "/v1/me") return me === "unavailable" ? Response.json(problem503, { status: 503 }) : Response.json(me);
    if (path === "/v1/plans") return Response.json([FREE, GROWTH]);
    if (path in extra) return Response.json(extra[path]);
    return Response.json({ title: "Not Found", status: 404, detail: "not found", correlation_id: "nf" }, { status: 404 });
  });
  return urls;
}

beforeEach(() => {
  nav.path = "/app/programs";
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("routing on GET /v1/me", () => {
  it("sends the operator to their window, a company not set up to setup, and the rest to the console", () => {
    expect(accountHome(OPERATOR)).toBe("/operator");
    expect(accountHome(NEW_COMPANY)).toBe("/app/welcome");
    expect(accountHome(COMPANY)).toBeNull();
    expect(consoleStart(OPERATOR)).toBe("/operator");
    expect(consoleStart(NEW_COMPANY)).toBe("/app/welcome");
    expect(consoleStart(UNAPPROVED)).toBe("/app/access");
    expect(consoleStart(COMPANY)).toBe("/app/programs");
  });

  it("names the company and its current plan for the sidebar", () => {
    expect(companyIdentity(FIRM, "ops@acme.example")).toEqual({ name: "Acme Kettles", detail: "Free plan · ops@acme.example" });
    expect(companyIdentity({ name: "Acme", effective_plan: "growth" })).toEqual({ name: "Acme", detail: "Growth plan" });
  });

  it("reads a 503 from /v1/me as sign-in verification being down, but not a missing website setting", () => {
    expect(isSignInUnavailable({ status: 503, title: "Service Unavailable" })).toBe(true);
    expect(isSignInUnavailable({ status: 503, title: "Not Configured" })).toBe(false);
    expect(isSignInUnavailable({ status: 502, title: "Bad Gateway" })).toBe(false);
  });
});

describe("the development console layout", () => {
  beforeEach(() => {
    for (const [k, v] of Object.entries(DEV)) vi.stubEnv(k, v);
  });
  const child = <p data-console-child>page</p>;

  it("sends the operator to /operator", async () => {
    serve(OPERATOR);
    expect(digestOf(await ConsoleLayout({ children: child }).catch((e: unknown) => e))).toContain("/operator");
  });

  it("shows setup with the API's plans in place of the page, and lets a Checkout return land", async () => {
    serve(NEW_COMPANY);
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).toContain("Set up your company");
    expect(html).toContain("$49.00 a month for each open product");
    expect(html).not.toContain("data-console-child");
    nav.path = "/app/billing/return";
    expect(renderToStaticMarkup(await ConsoleLayout({ children: child }))).toContain("data-console-child");
  });

  it("shows the retry state when sign-in verification is unavailable", async () => {
    serve("unavailable");
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).toContain("Sign-in verification is unavailable. Try again in a moment.");
    expect(html).not.toContain("data-console-child");
  });

  it("gates an unapproved company to Billing and Recall access, with the plan-full prompt", async () => {
    serve({ ...UNAPPROVED, firm: { ...UNAPPROVED.firm!, active_products: 1 } }, { "/v1/access": status({ requests: [] }) });
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).toContain("Request access to your recall");
    expect(html).toContain("data-upgrade-prompt");
    expect(html).toContain('href="/app/billing"');
    for (const label of ["Products", "Payouts"]) expect(html).not.toContain(`>${label}<`);
  });

  it("opens the console for an approved company, without reading /v1/access", async () => {
    const urls = serve(COMPANY);
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).toContain("data-console-child");
    expect(html).toContain("Acme Kettles");
    expect(urls.some((u) => u.endsWith("/v1/access"))).toBe(false);
  });
});

describe("console pages that only route", () => {
  beforeEach(() => {
    for (const [k, v] of Object.entries(DEV)) vi.stubEnv(k, v);
  });

  it("/app goes to setup, Recall access, or Products", async () => {
    for (const [me, to] of [
      [NEW_COMPANY, "/app/welcome"],
      [UNAPPROVED, "/app/access"],
      [COMPANY, "/app/programs"],
      [OPERATOR, "/operator"],
    ] as const) {
      serve(me);
      expect(digestOf(await ConsoleHome().catch((e: unknown) => e)), to).toContain(`;${to};`);
    }
  });

  it("/organization goes to /app, /app/operator to /operator, and setup to the console once done", async () => {
    expect(digestOf((() => { try { return OrganizationPage(); } catch (e) { return e; } })())).toContain(";/app;");
    expect(digestOf((() => { try { return OldOperatorPage(); } catch (e) { return e; } })())).toContain(";/operator;");
    serve(COMPANY);
    expect(digestOf(await WelcomePage().catch((e: unknown) => e))).toContain(";/app/programs;");
    serve(NEW_COMPANY);
    expect(renderToStaticMarkup(<>{await WelcomePage()}</>)).toContain("data-onboarding");
  });
});

describe("the operator window", () => {
  beforeEach(() => {
    for (const [k, v] of Object.entries(DEV)) vi.stubEnv(k, v);
  });

  it("sends a company account to /app", async () => {
    serve(COMPANY);
    expect(digestOf(await OperatorLayout({ children: <p /> }).catch((e: unknown) => e))).toContain(";/app;");
  });

  it("frames the operator's list with no console sidebar", async () => {
    serve(OPERATOR, { "/v1/operator/access": [OP] });
    const html = renderToStaticMarkup(await OperatorLayout({ children: <p data-operator-child /> }));
    expect(html).toContain("data-operator-window");
    expect(html).toContain("kartikeyamandhar777@gmail.com");
    expect(html).toContain("data-operator-child");
    expect(html).not.toContain('aria-label="Console"');
  });

  it("lists pending requests newest first with the company, email, plan, and products", async () => {
    serve(OPERATOR, { "/v1/operator/access": [OP, { ...OP, id: "o2", firm_name: "Later Co", created_at: "2026-10-22T05:00:00Z" }] });
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const html = renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{await OperatorPage()}</QueryClientProvider>);
    expect(html.indexOf("Later Co")).toBeLessThan(html.indexOf("Acme Kettles"));
    expect(html).toContain("ops@acme.example");
    expect(html).toContain("Recall 26999: Acme Recalls Kettles");
    expect(html).toContain("Free, 0 active products");
    expect(html.match(/data-operator-action="approved"/g)).toHaveLength(2);
    expect(html.match(/data-operator-action="rejected"/g)).toHaveLength(2);
  });

  it("warns about an unverified or test email, and folds decided requests away", () => {
    const html = renderToStaticMarkup(
      <OperatorQueue
        requests={[
          { ...OP, requester_email_verified: false },
          { ...OP, id: "o3", requester_test_address: true, requester_email: "x+clerk_test@acme.example" },
          { ...OP, id: "o4", status: "approved" },
        ]}
      />,
    );
    expect(html).toContain("This email is not verified.");
    expect(html).toContain("Test address: it proves nothing about the company.");
    expect(html).toMatch(/<details data-decided="true"><summary[^>]*>Decided \(1\)<\/summary>/);
    expect(html).toContain('data-operator-action="revoked"');
    expect(html).not.toContain("data-revoke-confirm"); // the confirm opens only after Revoke is pressed
  });

  it("polls the list every 10 seconds", async () => {
    vi.useFakeTimers();
    let calls = 0;
    vi.stubGlobal("fetch", async () => {
      calls += 1;
      return Response.json([OP]);
    });
    const client = new QueryClient();
    client.mount();
    const off = new QueryObserver(client, operatorQuery() as never).subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    const first = calls;
    await vi.advanceTimersByTimeAsync(OPERATOR_POLL_MS * 3);
    expect(OPERATOR_POLL_MS).toBe(10_000);
    expect(calls - first).toBe(3);
    off();
    client.unmount();
  });
});

describe("decision outcomes in plain words", () => {
  it("says what happened to the company's product", () => {
    expect(approvalOutcome({ status: "open", error: null })).toBe("Approved. The product is open and its claim link is ready.");
    expect(approvalOutcome({ status: null, error: "over_plan" })).toBe("Approved. The company's plan has no room for another product, so it stays closed until they upgrade.");
    expect(approvalOutcome({ status: "draft", error: "missing_refund_amount" })).toBe("Approved. The product waits for the company to set a refund amount.");
    for (const p of [{ status: "draft", error: "conflict" }, { status: null, error: "failed" }, null, undefined]) {
      expect(approvalOutcome(p)).toBe("Approved. The product could not be opened automatically.");
    }
    expect(decisionOutcome("rejected", null)).toMatch(/^Rejected\./);
    expect(decisionOutcome("revoked", null)).toMatch(/claim link stopped working/);
    expect(REVOKE_CONFIRM).toBe("Revoking pauses this company's product: its claim link and its claimants' links stop working until you approve it again.");
  });

  it("orders, labels, and warns", () => {
    expect(newestFirst([OP, { ...OP, id: "late", created_at: "2026-10-23T00:00:00Z" }]).map((r) => r.id)).toEqual(["late", "o1"]);
    expect(recallLabel(OP)).toBe("Recall 26999: Acme Recalls Kettles");
    expect(recallLabel({ ...OP, typed: true, title: "Kettle notice" })).toBe("Typed notice: Kettle notice");
    expect(emailWarnings(OP)).toEqual([]);
    expect(emailWarnings({ ...OP, requester_email: null })).toEqual(["No email was recorded for this request."]);
  });
});

describe("recall access and the company's products", () => {
  it("links an open product, explains a draft one, and offers to create a missing one", () => {
    expect(productState({ ...REQ, program_id: "p1", program_status: "open" })).toEqual({ kind: "open", href: "/app/programs/p1" });
    expect(productState({ ...REQ, program_id: "p1", program_status: "draft" })).toEqual({ kind: "draft", href: "/app/programs/p1" });
    expect(productState({ ...REQ, program_id: null })).toEqual({ kind: "create" });
    expect(productState({ ...REQ, status: "requested", program_id: null })).toEqual({ kind: "none" });
    const html = renderToStaticMarkup(
      <AccessRequests
        status={status({
          approved: true,
          requests: [
            { ...REQ, id: "x1", program_id: "p1", program_status: "open" },
            { ...REQ, id: "x2", program_id: "p2", program_status: "draft" },
            { ...REQ, id: "x3", program_id: null },
          ],
        })}
      />,
    );
    expect(html).toMatch(/href="\/app\/programs\/p1"[^>]*>Open product</);
    expect(html).toContain("Not open yet.");
    expect(html).toContain('href="/app/programs/p2"');
    expect(html).toContain('data-create-product="x3"');
    expect(html).toContain("Create the product");
  });

  it("creates the product from the recall, or from the typed notice with its approval", () => {
    expect(programBody(REQ)).toEqual({ recall_id: "r1" });
    expect(programBody({ ...REQ, recall_id: null, typed: true, typed_notice: "Acme recalls kettles" })).toEqual({
      manual: { title: "Acme Kettle Recall", description: "Acme recalls kettles" },
      access_id: "a1",
    });
  });

  it("says a full Free plan before a request is refused, counting waiting requests", () => {
    expect(planIsFull({ product_limit: 1, active_products: 1 }, [])).toBe(true);
    expect(planIsFull({ product_limit: 1, active_products: 0 }, [{ ...REQ, status: "requested" }])).toBe(true);
    expect(planIsFull({ product_limit: 1, active_products: 0 }, [])).toBe(false);
    expect(planIsFull({ product_limit: null, active_products: 5 }, [])).toBe(false);
    expect(planIsFull(null, [])).toBe(false);
  });

  it("answers a 409 before setup with the way to setup, and a 402 with the plan sentence and Billing", () => {
    const setup = { status: 409, title: "Conflict", detail: "finish setting up your company first: its name and plan", correlationId: null, retryAfterSeconds: null };
    expect(isNotOnboarded(setup)).toBe(true);
    const html = renderToStaticMarkup(<AccessProblem problem={setup} />);
    expect(html).toContain('href="/app/welcome"');
    // The words are fixed, whatever detail the API sends with the 402.
    const full = renderToStaticMarkup(<AccessProblem problem={{ ...setup, status: 402, detail: "free plan: active product limit reached" }} />);
    expect(full).toContain("Free covers one product. Upgrade to Growth to add another.");
    expect(full).not.toContain("limit reached");
    expect(full).toContain('href="/app/billing"');
    expect(renderToStaticMarkup(<AccessProblem problem={{ ...setup, detail: "this company already has an open request for this recall" }} />)).not.toContain("/app/welcome");
  });
});

describe("plans and setup", () => {
  it("builds both cards from GET /v1/plans, never from hardcoded prices", () => {
    const free = planCard(FREE);
    expect(free.features).toEqual([
      "One product at a time",
      "Unlimited claims",
      "The AI agent checks claims, up to a daily limit per product",
      "A claim link and QR code for the product",
      "Owner notices",
      "Refunds by bank transfer or mailed check, as the claimant chooses",
    ]);
    const growth = planCard(GROWTH);
    expect(growth.price).toBe("$49.00 a month for each open product");
    expect(growth.features).toContain("500 claims a month included, then $0.25 a claim");
    expect(growth.note).toBe(TEST_CARD_NOTE);
    expect(planCard({ ...GROWTH, program_price_cents: 9900 }).price).toBe("$99.00 a month for each open product");
    expect(planMoney(1250, "eur")).toBe("12.50 EUR");
    expect(productCount(1)).toBe("one product");
    expect(productCount(3)).toBe("3 products");
  });

  it("shows a plan by name when the API did not describe it", () => {
    expect(onboardingCards([FREE]).map((c) => [c.id, c.price])).toEqual([
      ["free", "No charge"],
      ["growth", null],
    ]);
  });

  it("checks the company name before sending it", () => {
    expect(companyName("  Acme   Kettles ")).toBe("Acme Kettles");
    expect(companyName("   ")).toBeNull();
    expect(companyName("x".repeat(201))).toBeNull();
    expect(onboardingBody(" Acme ", "growth")).toEqual({ name: "Acme", plan: "growth" });
    expect(onboardingBody("", "free")).toBeNull();
  });

  it("renders the name field, both plans, and the test card note", () => {
    const html = renderToStaticMarkup(<Onboarding plans={[FREE, GROWTH]} />);
    expect(html).toContain("Company name");
    expect(html).toContain('data-plan-card="free"');
    expect(html).toContain('data-plan-card="growth"');
    expect(html).toContain("4242 4242 4242 4242");
    expect(html).not.toMatch(/check only|by check only/i);
    const down = renderToStaticMarkup(<Onboarding plans={[]} plansProblem={{ status: 502, title: "Bad Gateway", detail: null, correlationId: null, retryAfterSeconds: null }} />);
    expect(down).toContain("data-plans-unavailable");
  });
});

describe("submitting setup (mocked API, no Stripe calls)", () => {
  function stub(res: () => Response) {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return res();
    });
    return seen;
  }
  const result = (checkout_url: string | null) => Response.json({ firm: FIRM, checkout_url });

  it("sends Free on to Recall access", async () => {
    const seen = stub(() => result(null));
    const navigate = vi.fn();
    expect(await submitOnboarding({ name: "Acme", plan: "free" }, navigate)).toEqual({ kind: "navigated" });
    expect(seen[0]!.url).toBe("/api/v1/onboarding");
    expect(seen[0]!.init.method).toBe("POST");
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({ name: "Acme", plan: "free" });
    expect(navigate).toHaveBeenCalledWith(AFTER_ONBOARDING);
    expect(AFTER_ONBOARDING).toBe("/app/access");
  });

  it("sends Growth to Stripe Checkout, and only to Stripe", async () => {
    stub(() => result("https://checkout.stripe.com/c/pay/cs_test_mock"));
    const navigate = vi.fn();
    await submitOnboarding({ name: "Acme", plan: "growth" }, navigate);
    expect(navigate).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_mock");
    for (const url of [null, "https://evil.example/checkout", "javascript:alert(1)"]) {
      stub(() => result(url));
      const nowhere = vi.fn();
      expect(await submitOnboarding({ name: "Acme", plan: "growth" }, nowhere)).toEqual({ kind: "on-free" });
      expect(nowhere).not.toHaveBeenCalled();
    }
  });

  it("goes to the console when the company was already set up, and returns any other problem", async () => {
    stub(() => Response.json({ title: "Conflict", status: 409, detail: "the company is already set up" }, { status: 409 }));
    const navigate = vi.fn();
    expect(await submitOnboarding({ name: "Acme", plan: "free" }, navigate)).toEqual({ kind: "navigated" });
    expect(navigate).toHaveBeenCalledWith("/app");
    stub(() => Response.json({ title: "Unprocessable", status: 422, detail: "the company name is empty" }, { status: 422 }));
    const outcome = await submitOnboarding({ name: "Acme", plan: "free" }, vi.fn());
    expect(outcome).toMatchObject({ kind: "problem", problem: { status: 422, detail: "the company name is empty" } });
  });
});

describe("navigation", () => {
  it("lists Products, Payouts, Billing, and Recall access, and only Billing and Recall access while gated", () => {
    expect(CONSOLE_NAV.map((i) => [i.label, i.href])).toEqual([
      ["Products", "/app/programs"],
      ["Payouts", "/app/payouts"],
      ["Billing", "/app/billing"],
      ["Recall access", "/app/access"],
    ]);
    expect(GATED_NAV.map((i) => i.label)).toEqual(["Billing", "Recall access"]);
  });

  it("marks the page's own item first, then the item that stands for other paths", () => {
    expect(currentHref(GATED_NAV, "/app/billing")).toBe("/app/billing");
    expect(currentHref(GATED_NAV, "/app/billing/return")).toBe("/app/billing");
    expect(currentHref(GATED_NAV, "/app/programs/p1")).toBe("/app/access");
    expect(currentHref(CONSOLE_NAV, "/app/claims/c1")).toBe("/app/programs");
    expect(currentHref(CONSOLE_NAV, "/operator")).toBeNull();
  });

  it("lets a gate show the page on allowed paths and its fallback elsewhere", () => {
    const gate = <PathGate allow={["/app/billing"]} fallback={<p>fallback</p>}><p>page</p></PathGate>;
    nav.path = "/app/billing/return";
    expect(renderToStaticMarkup(gate)).toBe("<p>page</p>");
    nav.path = "/app/billings";
    expect(renderToStaticMarkup(gate)).toBe("<p>fallback</p>");
  });
});

describe("accounts in Clerk mode", () => {
  beforeEach(() => {
    for (const [k, v] of Object.entries(CLERK)) vi.stubEnv(k, v);
  });

  it("lands sign-in and sign-up on /app", () => {
    expect(renderToStaticMarkup(SignInPage())).toContain('data-sign-in-to="/app"');
    expect(renderToStaticMarkup(SignUpPage())).toContain('data-sign-up-to="/app"');
  });

  it("shows the user menu without an organization switcher, and signs the operator out to sign-in", () => {
    const html = renderToStaticMarkup(<AccountControls />);
    expect(html).toContain("data-user-button");
    expect(html).not.toContain("org");
    expect(renderToStaticMarkup(<SignOutControl />)).toMatch(/data-sign-out-to="\/sign-in"[\s\S]*Sign out/);
  });
});

describe("wording", () => {
  it("drops team, administrator, and organization wording from the homepage and the plan sentences", () => {
    const text = renderToStaticMarkup(<Home />).replace(/<[^>]+>/g, " ");
    expect(text).not.toMatch(/team of four|teammate|organization/i);
    expect(humanSentence(402)).toBe("Your plan does not include this. Upgrade it under Billing.");
    expect(humanSentence(403)).not.toMatch(/administrator|role/i);
  });
});
