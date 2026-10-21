// Console authentication rules, pure so they are unit-tested. Clerk mode is the real one; dev mode reads the
// backend's X-Dev-* seam and refuses production. Anything else fails closed.
import type { Env } from "@/lib/api/config";

export type WebAuthMode = "dev" | "clerk";

/** null means not configured: console requests fail closed. */
export function webAuthMode(env: Env): WebAuthMode | null {
  const v = env.RECALLOPS_WEB_AUTH;
  return v === "dev" || v === "clerk" ? v : null;
}

/** Settings Clerk mode needs. Only names are ever reported, never values. */
export const CLERK_REQUIRED = ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY"] as const;

export function missingClerkSettings(env: Env): string[] {
  return CLERK_REQUIRED.filter((k) => !(env[k] ?? "").trim());
}

/** Clerk is active only in clerk mode with its keys present (keyless mode is disabled). */
export function clerkEnabled(env: Env): boolean {
  return webAuthMode(env) === "clerk" && missingClerkSettings(env).length === 0;
}

export type RouteKind = "public" | "console-page" | "console-api";

const startsWith = (p: string, prefix: string) => p === prefix || p.startsWith(`${prefix}/`);

/**
 * The claimant portal and its API, the sign-in pages, and the root stay public. Console pages require a session
 * (the proxy redirects to sign-in); console API calls are checked by the /api route, which answers 401.
 */
export function routeKind(pathname: string): RouteKind {
  if (startsWith(pathname, "/api/v1")) return "console-api";
  if (startsWith(pathname, "/app") || startsWith(pathname, "/organization")) return "console-page";
  return "public";
}

/**
 * Paths the Next proxy (src/proxy.ts) runs on. The homepage (/), the portal (/claim, /api/portal), and static assets
 * are excluded, so neither a first-time visitor nor a claimant touches Clerk at all. Kept here so a test can check it;
 * proxy.ts must repeat it literally, because Next reads the matcher statically.
 */
export const PROXY_MATCHER = "/((?!$|_next/|claim(?:/|$)|api/portal(?:/|$)|favicon\\.ico$).*)";

export type ConsoleRole = "admin" | "reviewer";

/** Clerk organization roles the backend accepts (auth/clerk.py strips the org: prefix). */
export function consoleRole(orgRole: string | null | undefined): ConsoleRole | null {
  const r = (orgRole ?? "").replace(/^org:/, "");
  return r === "admin" || r === "reviewer" ? r : null;
}

export type ClerkContext = { userId: string | null; orgId: string | null; orgRole: string | null };

export type ConsoleAccess =
  | { kind: "redirect"; to: string }
  | { kind: "no-access"; reason: string }
  | { kind: "ok"; role: ConsoleRole; orgId: string };

/** What the console shows a signed-in user; mirrors the backend's 403 rules so the UI never calls with a bad token. */
export function consoleAccess(ctx: ClerkContext): ConsoleAccess {
  if (!ctx.userId) return { kind: "redirect", to: "/sign-in" };
  if (!ctx.orgId) return { kind: "redirect", to: "/organization" };
  const role = consoleRole(ctx.orgRole);
  if (!role) {
    return { kind: "no-access", reason: "Your organization role has no access. Ask an admin for the admin or reviewer role." };
  }
  return { kind: "ok", role, orgId: ctx.orgId };
}
