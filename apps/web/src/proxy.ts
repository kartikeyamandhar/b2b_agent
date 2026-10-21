// Next.js 16 proxy (formerly middleware). In Clerk mode it runs clerkMiddleware on everything except the claimant
// portal and static files, and requires a session on console pages (redirect to sign-in). Console API calls under
// /api/v1 are not redirected: the /api route answers 401 itself when there is no session (auth-seam.ts). In dev mode
// it does nothing.
import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

import { clerkEnabled, routeKind } from "@/lib/auth/mode";

export const clerkHandler = clerkMiddleware(async (auth, req) => {
  if (routeKind(req.nextUrl.pathname) === "console-page") await auth.protect();
});

export default function proxy(req: NextRequest, event: NextFetchEvent) {
  if (!clerkEnabled(process.env)) return NextResponse.next();
  return clerkHandler(req, event);
}

// Must equal PROXY_MATCHER in src/lib/auth/mode.ts (Next reads this statically; a test compares them).
export const config = {
  matcher: ["/((?!_next/|claim(?:/|$)|api/portal(?:/|$)|favicon\\.ico$).*)"],
};
