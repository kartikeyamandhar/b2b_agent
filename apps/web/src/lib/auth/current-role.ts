// The console role on the server, to hide admin-only controls. The API still enforces every role (403).
import { devIdentity } from "@/lib/api/auth-seam";
import type { Env } from "@/lib/api/config";

import { clerkContext } from "./clerk-session";
import { webAuthMode, type ClerkContext, type ConsoleRole } from "./mode";

/** Plan v3: every signed-in company account is its admin. Development keeps the role its settings name. */
export async function currentConsoleRole(env: Env = process.env, context: () => Promise<ClerkContext> = clerkContext): Promise<ConsoleRole | null> {
  const mode = webAuthMode(env);
  if (mode === "dev") {
    try {
      return devIdentity(env).role;
    } catch {
      return null;
    }
  }
  if (mode === "clerk") return (await context()).userId ? "admin" : null;
  return null;
}
