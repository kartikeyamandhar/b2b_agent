// Server-side Clerk session access. Imported lazily, so dev mode and the claimant portal never load Clerk.
import type { ClerkContext } from "./mode";

// One import shared by concurrent callers (a page reads the role and the token at the same time).
let clerkServer: Promise<typeof import("@clerk/nextjs/server")> | null = null;
const loadClerkServer = () => (clerkServer ??= import("@clerk/nextjs/server"));

/** The signed-in user's session token for the API (Authorization: Bearer), or null without a session. */
export async function clerkSessionToken(): Promise<string | null> {
  const { auth } = await loadClerkServer();
  const a = await auth();
  if (!a.userId) return null;
  return (await a.getToken()) ?? null;
}

/** Who is signed in. Plan v3 reads no organization: one account is one company. */
export async function clerkContext(): Promise<ClerkContext> {
  const { auth } = await loadClerkServer();
  const a = await auth();
  return { userId: a.userId ?? null };
}
