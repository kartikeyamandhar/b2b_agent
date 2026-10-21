// The console role on the server, to hide admin-only controls. The API still enforces every role (403).
import { devIdentity } from "@/lib/api/auth-seam";
import type { Env } from "@/lib/api/config";

import { clerkContext } from "./clerk-session";
import { consoleRole, webAuthMode, type ClerkContext, type ConsoleRole } from "./mode";

export async function currentConsoleRole(env: Env = process.env, context: () => Promise<ClerkContext> = clerkContext): Promise<ConsoleRole | null> {
  const mode = webAuthMode(env);
  if (mode === "dev") {
    try {
      return devIdentity(env).role;
    } catch {
      return null;
    }
  }
  if (mode === "clerk") return consoleRole((await context()).orgRole);
  return null;
}
