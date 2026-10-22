// Clerk mode end to end inside the web server: the proxy, the /api route, the console gate, and the portal.
// @clerk/nextjs is mocked: its server entry refuses to load outside Next, and no test may call Clerk.
import { readFileSync } from "node:fs";

import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clerk = vi.hoisted(() => ({
  session: null as null | { userId: string | null; orgId: string | null; orgRole: string | null; token: string | null },
  authCalls: 0,
  handler: null as null | ((auth: unknown, req: unknown) => Promise<void>),
  middlewareCalls: 0,
}));

vi.mock("@clerk/nextjs/server", () => ({
  auth: async () => {
    clerk.authCalls += 1;
    const s = clerk.session ?? { userId: null, orgId: null, orgRole: null, token: null };
    return { userId: s.userId, orgId: s.orgId, orgRole: s.orgRole, getToken: async () => s.token };
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
  OrganizationSwitcher: () => <div data-org-switcher />,
  UserButton: () => <div data-user-button />,
}));
vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import { GET, POST } from "@/app/api/[...path]/route";
import ProgramsPage from "@/app/app/programs/page";
import ClaimStatusPage from "@/app/claim/[token]/status/page";
import { Providers } from "@/app/app/providers";
import { ClerkConsole } from "@/components/ClerkConsole";

// The organization is approved to run a recall (company verification), so the console renders its children.
const APPROVED = async () => ({ ok: true as const, data: { required: true, approved: true, operator: false, requests: [] } });
import { PROXY_MATCHER } from "@/lib/auth/mode";
import proxy, { config } from "@/proxy";

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
const signIn = (orgRole = "org:admin") => {
  clerk.session = { userId: "user_1", orgId: "org_1", orgRole, token: "header.payload.signature" };
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
    signIn("org:reviewer");
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

describe("console gate", () => {
  const render = async (s: { userId: string | null; orgId: string | null; orgRole: string | null }) =>
    renderToStaticMarkup(await ClerkConsole({ children: <p data-console-child>queue</p>, context: async () => s, recallAccess: APPROVED }));

  it("sends a user without an organization to the organization page", async () => {
    const err = await render({ userId: "u", orgId: null, orgRole: null }).catch((e: unknown) => e);
    expect(String((err as { digest?: string }).digest)).toContain("/organization");
  });

  it("sends a signed-out visitor to sign-in", async () => {
    const err = await render({ userId: null, orgId: null, orgRole: null }).catch((e: unknown) => e);
    expect(String((err as { digest?: string }).digest)).toContain("/sign-in");
  });

  it("stops a member role before any console content renders", async () => {
    const html = await render({ userId: "u", orgId: "org_1", orgRole: "org:member" });
    expect(html).toContain("No access to this organization");
    expect(html).not.toContain("data-console-child");
    expect(html).toContain("data-org-switcher");
  });

  it("gives each organization its own query cache, so switching never shows another firm's data", async () => {
    const tree = (await ClerkConsole({ children: <p />, context: async () => ({ userId: "u", orgId: "org_42", orgRole: "org:admin" }), recallAccess: APPROVED })) as {
      props: { children: { key: string | null; type: unknown } };
    };
    const providers = tree.props.children;
    expect(providers.key).toBe("org_42");
    expect(providers.type).toBe(Providers);
  });

  it("renders the console with account controls for a reviewer", async () => {
    const html = await render({ userId: "u", orgId: "org_1", orgRole: "org:reviewer" });
    expect(html).toContain("data-console-child");
    expect(html).toContain("Reviewer");
    expect(html).toContain("data-org-switcher");
    expect(html).toContain("data-user-button");
    expect(html).toContain("data-clerk-provider");
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
    for (const [path, protectedRoute] of [["/app/programs", true], ["/organization", true], ["/api/v1/programs", false], ["/sign-in", false], ["/", false]] as const) {
      const protect = vi.fn(async () => {});
      await clerk.handler!(Object.assign(async () => ({}), { protect }), { nextUrl: { pathname: path } });
      expect(protect.mock.calls.length > 0, path).toBe(protectedRoute);
    }
  });
});
