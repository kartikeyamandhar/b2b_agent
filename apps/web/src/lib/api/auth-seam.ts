// The single place console requests get their identity. Today: the backend's dev provider headers
// (AUTH_PROVIDER=dev). The login owner (component 4) replaces consoleAuthHeaders with the Clerk session token;
// nothing else in apps/web reads identity. This file is UI-owned scaffolding, deliberately outside
// src/lib/auth/ (login-owned in CODEOWNERS).
//
// The firm and role come from server settings only, never from the browser: forward.ts drops any X-Dev-* or
// Authorization header a client sends.
import { ConfigError, type Env } from "./config";

export type ConsoleRole = "admin" | "reviewer";

export type ConsoleIdentity = {
  firmId: string;
  role: ConsoleRole;
  userId: string;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Fails closed: anything but an explicit, well-formed dev configuration throws. */
export function devIdentity(env: Env = process.env): ConsoleIdentity {
  const mode = env.RECALLOPS_WEB_AUTH;
  if (mode !== "dev") {
    throw new ConfigError(
      "console auth is not configured: set RECALLOPS_WEB_AUTH=dev for local development (Clerk is component 4)",
    );
  }
  if (env.VERCEL_ENV === "production") {
    throw new ConfigError("the dev auth seam refuses to run in a production deployment");
  }
  const firmId = env.RECALLOPS_DEV_FIRM_ID ?? "";
  if (!UUID.test(firmId)) throw new ConfigError("RECALLOPS_DEV_FIRM_ID must be a UUID");
  const role = env.RECALLOPS_DEV_ROLE ?? "admin";
  if (role !== "admin" && role !== "reviewer") throw new ConfigError("RECALLOPS_DEV_ROLE must be admin or reviewer");
  const userId = env.RECALLOPS_DEV_USER_ID ?? "dev-user";
  if (!/^[\w.@-]{1,64}$/.test(userId)) throw new ConfigError("RECALLOPS_DEV_USER_ID has unsupported characters");
  return { firmId, role, userId };
}

export async function consoleAuthHeaders(env: Env = process.env): Promise<Record<string, string>> {
  const id = devIdentity(env);
  return { "X-Dev-Firm-Id": id.firmId, "X-Dev-Role": id.role, "X-Dev-User-Id": id.userId };
}
