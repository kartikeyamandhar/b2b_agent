// Next.js 16 proxy (formerly middleware). In Clerk mode it runs clerkMiddleware on everything except the homepage,
// the claimant portal, and static files, and requires a session on console pages (redirect to sign-in). Console API
// calls under /api/v1 are not redirected: the /api route answers 401 itself when there is no session (auth-seam.ts).
// In dev mode it does nothing.
import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

import { isLoopbackHost } from "@/lib/api/config";
import { clerkEnabled, routeKind, webAuthMode } from "@/lib/auth/mode";

export const clerkHandler = clerkMiddleware(async (auth, req) => {
  if (routeKind(req.nextUrl.pathname) === "console-page") await auth.protect();
});

/** In dev mode every console request carries an admin identity, so it answers only to this machine: no other host
 *  on the network, and no page that rebinds its own name to 127.0.0.1 (review of PR 9). */
export function devHostAllowed(host: string | null): boolean {
  return isLoopbackHost(host);
}

export default function proxy(req: NextRequest, event: NextFetchEvent) {
  if (webAuthMode(process.env) === "dev" && !devHostAllowed(req.headers.get("host"))) {
    return new NextResponse("The development console answers only on localhost.", { status: 403 });
  }
  if (!clerkEnabled(process.env)) return NextResponse.next();
  return clerkHandler(req, event);
}

// Must equal PROXY_MATCHER in src/lib/auth/mode.ts (Next reads this statically; a test compares them).
export const config = {
  matcher: ["/((?!$|_next/|claim(?:/|$)|api/portal(?:/|$)|favicon\\.ico$).*)"],
};
