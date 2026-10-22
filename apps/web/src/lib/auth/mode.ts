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
 * The claimant portal and its API, the public product link pages, the sign-in pages, and the root stay public.
 * Console pages (the operator window included) require a session (the proxy redirects to sign-in); console API calls
 * are checked by the /api route, which answers 401.
 */
export function routeKind(pathname: string): RouteKind {
  if (startsWith(pathname, "/api/v1")) return "console-api";
  if (startsWith(pathname, "/app") || startsWith(pathname, "/organization") || startsWith(pathname, "/operator")) return "console-page";
  return "public";
}

/**
 * Paths the Next proxy (src/proxy.ts) runs on. The homepage (/), the portal (/claim, /api/portal), the public product
 * link pages (/file), and static assets are excluded, so neither a first-time visitor nor a claimant touches Clerk at
 * all. Kept here so a test can check it; proxy.ts must repeat it literally, because Next reads the matcher statically.
 */
export const PROXY_MATCHER = "/((?!$|_next/|claim(?:/|$)|file(?:/|$)|api/portal(?:/|$)|favicon\\.ico$|icon\\.svg$|apple-icon\\.png$).*)";

/** Plan v3: one account is one company, so every signed-in company account is its admin (the API ignores
 *  organization claims). The development seam may still send reviewer (auth-seam.ts). */
export type ConsoleRole = "admin" | "reviewer";

export type ClerkContext = { userId: string | null };

export type ConsoleAccess = { kind: "redirect"; to: string } | { kind: "ok"; userId: string };

/** A session is all the console asks of Clerk; GET /v1/me then says whether this is the operator or which company. */
export function consoleAccess(ctx: ClerkContext): ConsoleAccess {
  if (!ctx.userId) return { kind: "redirect", to: "/sign-in" };
  return { kind: "ok", userId: ctx.userId };
}
