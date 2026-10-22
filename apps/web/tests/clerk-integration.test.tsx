// Clerk mode end to end inside the web server: the proxy, the /api route, the console gate, and the portal.
// @clerk/nextjs is mocked: its server entry refuses to load outside Next, and no test may call Clerk.
import { readFileSync } from "node:fs";

import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clerk = vi.hoisted(() => ({
  session: null as null | { userId: string | null; token: string | null },
  authCalls: 0,
  handler: null as null | ((auth: unknown, req: unknown) => Promise<void>),
  middlewareCalls: 0,
  path: "/app/programs",
}));

vi.mock("@clerk/nextjs/server", () => ({
  auth: async () => {
    clerk.authCalls += 1;
    const s = clerk.session ?? { userId: null, token: null };
    return { userId: s.userId, getToken: async () => s.token };
  },
  clerkMiddleware: (h: (auth: unknown, req: unknown) => Promise<void>) => {
    clerk.handler = h;
    return () => {
      clerk.middlewareCalls += 1;
      return new Response(null, { headers: { "x-clerk-ran": "1" } });
    };
  },
}));
vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => <div data-clerk-provider>{children}</div>,
  UserButton: () => <div data-user-button />,
  SignOutButton: ({ children }: { children: ReactNode }) => <div data-clerk-sign-out>{children}</div>,
}));
vi.mock("next/navigation", async (orig) => ({
  ...(await orig<typeof import("next/navigation")>()),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => clerk.path,
}));

import { GET, POST } from "@/app/api/[...path]/route";
import ProgramsPage from "@/app/app/programs/page";
import ClaimStatusPage from "@/app/claim/[token]/status/page";
import { Providers } from "@/app/app/providers";
import { ClerkConsole, ClerkOperatorWindow } from "@/components/ClerkConsole";
import type { Me, MeFirm, PlanInfo } from "@/lib/api-types";
import type { Loaded } from "@/lib/api/queries";
import { PROXY_MATCHER } from "@/lib/auth/mode";
import proxy, { config } from "@/proxy";

const FIRM: MeFirm = {
  id: "00000000-0000-4000-8000-00000000f001",
  name: "Acme Kettles",
  onboarded: true,
  plan: "free",
  effective_plan: "free",
  subscription_status: null,
  approved: true,
  active_products: 1,
  product_limit: 1,
};
const meOf = (m: Me) => async (): Promise<Loaded<Me>> => ({ ok: true, data: m });
const COMPANY = meOf({ operator: false, email: "ops@acme.example", firm: FIRM });
const OPERATOR = meOf({ operator: true, email: "operator@example.com", firm: null });
const NOT_SET_UP = meOf({ operator: false, email: "new@acme.example", firm: { ...FIRM, name: "New company", onboarded: false, approved: false, active_products: 0 } });
const UNAPPROVED = meOf({ operator: false, email: "ops@acme.example", firm: { ...FIRM, approved: false, active_products: 0 } });
const UNAVAILABLE = async (): Promise<Loaded<Me>> => ({ ok: false, problem: { status: 503, title: "Service Unavailable", detail: "sign-in verification unavailable", correlationId: "c-503", retryAfterSeconds: null } });
const PLANS = async (): Promise<Loaded<PlanInfo[]>> => ({
  ok: true,
  data: [
    { id: "free", product_limit: 1, program_price_cents: null, included_claims: null, claim_price_cents: null, currency: "usd", refund_options: ["stripe_connect", "check_export"] },
    { id: "growth", product_limit: null, program_price_cents: 4900, included_claims: 500, claim_price_cents: 25, currency: "usd", refund_options: ["stripe_connect", "check_export"] },
  ],
});
const PENDING_ACCESS = async () => ({ ok: true as const, data: { required: true, approved: false, operator: false, requests: [] } });

const ENV = {
  RECALLOPS_API_URL: "http://127.0.0.1:8000",
  RECALLOPS_WEB_AUTH: "clerk",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_placeholder",
  CLERK_SECRET_KEY: "sk_test_placeholder",
};
const programs = readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8");
const portalView = readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8");
const T = "a".repeat(96);
let calls: Array<{ url: string; headers: Headers }>;

beforeEach(() => {
  calls = [];
  clerk.session = null;
  clerk.authCalls = 0;
  clerk.middlewareCalls = 0;
  clerk.path = "/app/programs";
  for (const [k, v] of Object.entries(ENV)) vi.stubEnv(k, v);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, headers: new Headers(init.headers) });
    return new Response(url.includes("/portal/") ? portalView : programs, { headers: { "content-type": "application/json" } });
  });
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });
const signIn = () => {
  clerk.session = { userId: "user_1", token: "header.payload.signature" };
};

describe("/api proxy in Clerk mode", () => {
  it("forwards the verified session token and drops everything the browser sent", async () => {
    signIn();
    const req = new Request("http://localhost:3000/api/v1/programs", {
      headers: { authorization: "Bearer forged", "x-dev-firm-id": "00000000-0000-4000-8000-00000000d002", "x-dev-role": "admin" },
    });
    const res = await GET(req, ctx(["v1", "programs"]));
    expect(res.status).toBe(200);
    const sent = calls[0]!.headers;
    expect(sent.get("authorization")).toBe("Bearer header.payload.signature");
    expect(sent.get("x-dev-firm-id")).toBeNull();
    expect(sent.get("x-dev-role")).toBeNull();
  });

  it("fails closed with a 401 problem and never calls the API without a session", async () => {
    const res = await GET(new Request("http://localhost:3000/api/v1/programs"), ctx(["v1", "programs"]));
    expect(res.status).toBe(401);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(((await res.json()) as { detail: string }).detail).toBe("sign in to continue");
    expect(calls).toHaveLength(0);
  });

  it("keeps the claimant portal public: no session lookup, no token", async () => {
    signIn();
    const res = await GET(new Request(`http://localhost:3000/api/portal/${T}`), ctx(["portal", T]));
    expect(res.status).toBe(200);
    expect(clerk.authCalls).toBe(0);
    expect(calls[0]!.headers.get("authorization")).toBeNull();
  });

  it("still refuses cross-site writes, now that a session cookie exists", async () => {
    signIn();
    const req = new Request("http://localhost:3000/api/v1/claims/c/actions", {
      method: "POST",
      headers: { origin: "https://evil.example", host: "localhost:3000", "content-type": "application/json" },
      body: "{}",
    });
    expect((await POST(req, ctx(["v1", "claims", "c", "actions"]))).status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});

describe("pages in Clerk mode", () => {
  it("a console page without a session shows sign-in, not data, and calls nothing", async () => {
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).toContain("Your session has ended. Sign in again to continue.");
    expect(calls).toHaveLength(0);
  });

  it("a console page with a session calls the API with the bearer token", async () => {
    signIn();
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).toContain("Scotch Thermal Laminators");
    expect(calls[0]!.headers.get("authorization")).toBe("Bearer header.payload.signature");
  });

  it("the claimant status page needs no session", async () => {
    const html = renderToStaticMarkup(await ClaimStatusPage({ params: Promise.resolve({ token: T }) }));
    expect(html).toContain("Example Thermal Laminators");
    expect(clerk.authCalls).toBe(0);
    expect(calls[0]!.headers.get("authorization")).toBeNull();
  });
});

const digestOf = (err: unknown) => String((err as { digest?: string }).digest);

describe("console gate (plan v3)", () => {
  const child = <p data-console-child>queue</p>;
  const render = async (me = COMPANY, userId: string | null = "user_1", recallAccess = PENDING_ACCESS) =>
    renderToStaticMarkup(await ClerkConsole({ children: child, context: async () => ({ userId }), me, recallAccess, plans: PLANS }));

  it("sends a signed-out visitor to sign-in without asking the API who they are", async () => {
    let asked = false;
    const err = await render(async () => ((asked = true), COMPANY()), null).catch((e: unknown) => e);
    expect(digestOf(err)).toContain("/sign-in");
    expect(asked).toBe(false);
  });

  it("sends the operator to their window from every console page", async () => {
    for (const path of ["/app", "/app/programs", "/app/billing", "/app/access"]) {
      clerk.path = path;
      const err = await render(OPERATOR).catch((e: unknown) => e);
      expect(digestOf(err), path).toContain("/operator");
    }
  });

  it("shows setup in place of any page until the company has a name and a plan, with no console items", async () => {
    const html = await render(NOT_SET_UP);
    expect(html).toContain("data-onboarding");
    expect(html).toContain("Set up your company");
    expect(html).not.toContain("data-console-child");
    for (const label of ["Products", "Payouts", "Billing", "Recall access"]) expect(html).not.toContain(`>${label}<`);
  });

  it("still lands a Stripe Checkout return during setup", async () => {
    clerk.path = "/app/billing/return";
    const html = await render(NOT_SET_UP);
    expect(html).toContain("data-console-child");
    expect(html).not.toContain("data-onboarding");
  });

  it("shows a short retry state, never a crash, when sign-in verification is unavailable", async () => {
    const html = await render(UNAVAILABLE);
    expect(html).toContain("Sign-in verification is unavailable. Try again in a moment.");
    expect(html).toContain('href="/app"');
    expect(html).not.toContain("data-console-child");
  });

  it("offers only Billing and Recall access until the operator approves the company, and opens Billing", async () => {
    const html = await render(UNAPPROVED);
    expect(html).not.toContain("data-console-child");
    expect(html).toContain("Request access to your recall");
    for (const label of ["Billing", "Recall access"]) expect(html).toContain(`>${label}<`);
    for (const label of ["Products", "Payouts", "Operator"]) expect(html).not.toContain(`>${label}<`);
    clerk.path = "/app/billing";
    expect(await render(UNAPPROVED)).toContain("data-console-child");
    clerk.path = "/app/access";
    expect(await render(UNAPPROVED)).toContain("data-console-child");
  });

  it("opens the whole console to an approved company, with no organization or operator items", async () => {
    let read = false;
    const html = await render(COMPANY, "user_1", async () => ((read = true), PENDING_ACCESS()));
    expect(html).toContain("data-console-child");
    for (const label of ["Products", "Payouts", "Billing", "Recall access"]) expect(html).toContain(`>${label}<`);
    expect(html).not.toContain('href="/operator"');
    expect(html).not.toMatch(/organization|teammate|member|seat/i);
    expect(html).toContain("Acme Kettles");
    expect(html).toContain("Free plan · ops@acme.example");
    expect(read).toBe(false); // GET /v1/me already said approved
  });

  it("gives each account its own query cache, so another account never sees cached data", async () => {
    const tree = (await ClerkConsole({ children: <p />, context: async () => ({ userId: "user_42" }), me: COMPANY })) as {
      props: { children: { key: string | null; type: unknown } };
    };
    const providers = tree.props.children;
    expect(providers.key).toBe("user_42");
    expect(providers.type).toBe(Providers);
  });

  it("renders the user menu and no organization switcher", async () => {
    const html = await render();
    expect(html).toContain("data-user-button");
    expect(html).toContain("data-clerk-provider");
    expect(html).not.toContain("data-org-switcher");
  });
});

describe("the operator window", () => {
  const frame = async (me = OPERATOR, userId: string | null = "user_op") =>
    renderToStaticMarkup(await ClerkOperatorWindow({ children: <p data-operator-child>queue</p>, context: async () => ({ userId }), me }));

  it("is standalone: the mark, Operator, the account email, and Sign out, with no console sidebar", async () => {
    const html = await frame();
    expect(html).toContain("data-operator-window");
    expect(html).toContain(">Operator<");
    expect(html).toContain("operator@example.com");
    expect(html).toContain("Sign out");
    expect(html).toContain("data-operator-child");
    expect(html).not.toContain('aria-label="Console"');
    for (const label of ["Products", "Payouts", "Billing", "Recall access"]) expect(html).not.toContain(`>${label}<`);
  });

  it("sends a company account to its console, and a signed-out visitor to sign-in", async () => {
    expect(digestOf(await frame(COMPANY).catch((e: unknown) => e))).toContain("/app");
    expect(digestOf(await frame(COMPANY).catch((e: unknown) => e))).not.toContain("/operator");
    expect(digestOf(await frame(OPERATOR, null).catch((e: unknown) => e))).toContain("/sign-in");
  });

  it("shows the retry state when sign-in verification is unavailable", async () => {
    const html = await frame(UNAVAILABLE);
    expect(html).toContain("Sign-in verification is unavailable. Try again in a moment.");
    expect(html).toContain('href="/operator"');
    expect(html).not.toContain("data-operator-child");
  });
});

describe("proxy.ts", () => {
  const req = (path: string, host = "localhost:3000") => ({ nextUrl: { pathname: path }, headers: new Headers({ host }) }) as never;

  it("uses the same matcher the tests check", () => {
    expect(config.matcher).toEqual([PROXY_MATCHER]);
  });

  it("runs Clerk only in Clerk mode", () => {
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    const res = proxy(req("/app/programs"), {} as never) as Response;
    expect(clerk.middlewareCalls).toBe(0);
    expect(res.headers.get("x-middleware-next")).toBe("1");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "clerk");
    proxy(req("/app/programs"), {} as never);
    expect(clerk.middlewareCalls).toBe(1);
  });

  it("answers dev mode only on this machine (no LAN, no DNS rebinding)", () => {
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    for (const host of ["localhost:3000", "127.0.0.1:3000", "[::1]:3000"]) {
      expect((proxy(req("/app/programs", host), {} as never) as Response).status, host).not.toBe(403);
    }
    for (const host of ["192.168.1.20:3000", "attacker.example:3000", "localhost.attacker.example"]) {
      expect((proxy(req("/api/v1/programs", host), {} as never) as Response).status, host).toBe(403);
    }
  });

  it("protects console pages and leaves the API route and sign-in to answer for themselves", async () => {
    for (const [path, protectedRoute] of [["/app/programs", true], ["/operator", true], ["/organization", true], ["/api/v1/programs", false], ["/sign-in", false], ["/file/tok", false], ["/", false]] as const) {
      const protect = vi.fn(async () => {});
      await clerk.handler!(Object.assign(async () => ({}), { protect }), { nextUrl: { pathname: path } });
      expect(protect.mock.calls.length > 0, path).toBe(protectedRoute);
    }
  });
});
