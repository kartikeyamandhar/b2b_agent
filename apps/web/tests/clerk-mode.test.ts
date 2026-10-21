import { describe, expect, it } from "vitest";

import { NotSignedIn, consoleAuthHeaders } from "@/lib/api/auth-seam";
import { ConfigError } from "@/lib/api/config";
import {
  PROXY_MATCHER,
  clerkEnabled,
  consoleAccess,
  consoleRole,
  missingClerkSettings,
  routeKind,
  webAuthMode,
} from "@/lib/auth/mode";

// Placeholder values only: these are not keys.
const CLERK = { RECALLOPS_WEB_AUTH: "clerk", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_placeholder", CLERK_SECRET_KEY: "sk_test_placeholder" };

describe("mode and settings", () => {
  it("knows dev and clerk, and fails closed on anything else", () => {
    expect(webAuthMode({ RECALLOPS_WEB_AUTH: "clerk" })).toBe("clerk");
    expect(webAuthMode({ RECALLOPS_WEB_AUTH: "dev" })).toBe("dev");
    for (const v of [undefined, "", "Clerk", "none"]) expect(webAuthMode({ RECALLOPS_WEB_AUTH: v })).toBeNull();
  });

  it("reports missing Clerk settings by name, never by value", () => {
    expect(missingClerkSettings(CLERK)).toEqual([]);
    expect(missingClerkSettings({ ...CLERK, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: " " })).toEqual(["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"]);
    expect(clerkEnabled({ RECALLOPS_WEB_AUTH: "clerk" })).toBe(false);
    expect(clerkEnabled(CLERK)).toBe(true);
    expect(clerkEnabled({ ...CLERK, RECALLOPS_WEB_AUTH: "dev" })).toBe(false);
  });
});

describe("routes", () => {
  it.each([
    ["/app", "console-page"],
    ["/app/programs/p/claims", "console-page"],
    ["/organization", "console-page"],
    ["/api/v1/programs", "console-api"],
    ["/claim/tok", "public"],
    ["/claim/tok/status", "public"],
    ["/api/portal/tok/photos", "public"],
    ["/sign-in", "public"],
    ["/sign-in/factor-one", "public"],
    ["/sign-up", "public"],
    ["/", "public"],
    ["/application", "public"],
  ])("%s is %s", (path, kind) => {
    expect(routeKind(path)).toBe(kind);
  });

  const matcher = new RegExp(`^${PROXY_MATCHER}$`);

  it("runs the proxy on the console, the API proxy, and sign-in", () => {
    for (const p of ["/", "/app/programs", "/api/v1/programs", "/sign-in", "/sign-up/verify", "/organization", "/app/claims/c/trace"]) {
      expect(matcher.test(p), p).toBe(true);
    }
  });

  it("never runs it on the claimant portal or static files", () => {
    for (const p of ["/claim", "/claim/tok", "/claim/tok/status", "/claim/connect-return", "/api/portal/tok", "/api/portal/tok/photos", "/_next/static/chunks/a.js", "/favicon.ico"]) {
      expect(matcher.test(p), p).toBe(false);
    }
  });
});

describe("roles (backend auth/clerk.py)", () => {
  it.each([
    ["org:admin", "admin"],
    ["admin", "admin"],
    ["org:reviewer", "reviewer"],
    ["reviewer", "reviewer"],
    ["org:member", null],
    ["org:admin2", null],
    ["", null],
    [null, null],
  ])("%s -> %s", (orgRole, role) => {
    expect(consoleRole(orgRole)).toBe(role);
  });

  it("sends a user without an organization to create one, and stops other roles", () => {
    expect(consoleAccess({ userId: null, orgId: null, orgRole: null })).toEqual({ kind: "redirect", to: "/sign-in" });
    expect(consoleAccess({ userId: "u", orgId: null, orgRole: null })).toEqual({ kind: "redirect", to: "/organization" });
    expect(consoleAccess({ userId: "u", orgId: "org_1", orgRole: "org:member" }).kind).toBe("no-access");
    expect(consoleAccess({ userId: "u", orgId: "org_1", orgRole: "org:reviewer" })).toEqual({ kind: "ok", role: "reviewer", orgId: "org_1" });
  });
});

describe("auth seam in Clerk mode", () => {
  it("sends only the session token, never development headers", async () => {
    const env = { ...CLERK, RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001", RECALLOPS_DEV_ROLE: "admin" };
    expect(await consoleAuthHeaders(env, async () => "header.payload.sig")).toEqual({ Authorization: "Bearer header.payload.sig" });
  });

  it("fails closed without a session", async () => {
    await expect(consoleAuthHeaders(CLERK, async () => null)).rejects.toBeInstanceOf(NotSignedIn);
  });

  it("refuses to run without the Clerk keys and names only the missing setting", async () => {
    const err = await consoleAuthHeaders({ RECALLOPS_WEB_AUTH: "clerk", CLERK_SECRET_KEY: "sk_test_SHOULD_NOT_APPEAR" }, async () => "t").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ConfigError);
    expect(String(err)).toContain("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
    expect(String(err)).not.toContain("SHOULD_NOT_APPEAR");
  });

  it("does not ask for a session in dev mode, and fails closed when unset", async () => {
    let asked = false;
    const dev = { RECALLOPS_WEB_AUTH: "dev", RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001" };
    expect(await consoleAuthHeaders(dev, async () => ((asked = true), "t"))).toHaveProperty("X-Dev-Firm-Id");
    expect(asked).toBe(false);
    await expect(consoleAuthHeaders({}, async () => "t")).rejects.toBeInstanceOf(ConfigError);
    await expect(consoleAuthHeaders({ ...dev, VERCEL_ENV: "production" }, async () => "t")).rejects.toBeInstanceOf(ConfigError);
  });
});
