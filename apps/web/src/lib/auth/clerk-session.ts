// Server-side Clerk session access. Imported lazily, so dev mode and the claimant portal never load Clerk.
import type { ClerkContext } from "./mode";

/** The signed-in user's session token for the API (Authorization: Bearer), or null without a session. */
export async function clerkSessionToken(): Promise<string | null> {
  const { auth } = await import("@clerk/nextjs/server");
  const a = await auth();
  if (!a.userId) return null;
  return (await a.getToken()) ?? null;
}

export async function clerkContext(): Promise<ClerkContext> {
  const { auth } = await import("@clerk/nextjs/server");
  const a = await auth();
  return { userId: a.userId ?? null, orgId: a.orgId ?? null, orgRole: a.orgRole ?? null };
}
