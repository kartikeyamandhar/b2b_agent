// The single place console requests get their identity, on the server only.
// - clerk: the signed-in user's Clerk session token as Authorization: Bearer. The API finds the account's company
//   from the user (plan v3: one account is one company). No session means NotSignedIn: the request fails closed with
//   a 401.
// - dev: the backend's dev provider headers (AUTH_PROVIDER=dev), never in production. The role defaults to admin.
// - anything else: ConfigError. The browser never chooses an identity: forward.ts drops X-Dev-* and Authorization.
import { clerkSessionToken } from "@/lib/auth/clerk-session";
import { missingClerkSettings, webAuthMode } from "@/lib/auth/mode";

import { apiConfig, ConfigError, isLoopbackHost, type Env } from "./config";

export type ConsoleRole = "admin" | "reviewer";

export type ConsoleIdentity = {
  firmId: string;
  role: ConsoleRole;
  userId: string;
};

/** No Clerk session on a console request. */
export class NotSignedIn extends Error {
  constructor() {
    super("sign in to continue");
    this.name = "NotSignedIn";
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Fails closed: anything but an explicit, well-formed dev configuration throws. */
export function devIdentity(env: Env = process.env): ConsoleIdentity {
  if (webAuthMode(env) !== "dev") {
    throw new ConfigError("console auth is not configured for development headers: set RECALLOPS_WEB_AUTH=dev or clerk");
  }
  // Any deployment (preview included), or an API on another machine, means real users could reach this identity.
  if (env.VERCEL_ENV) {
    throw new ConfigError("the dev auth seam refuses to run in a deployment");
  }
  if (!isLoopbackHost(new URL(apiConfig(env).baseUrl).hostname)) {
    throw new ConfigError("the dev auth seam only talks to an API on this machine");
  }
  const firmId = env.RECALLOPS_DEV_FIRM_ID ?? "";
  if (!UUID.test(firmId)) throw new ConfigError("RECALLOPS_DEV_FIRM_ID must be a UUID");
  const role = env.RECALLOPS_DEV_ROLE ?? "admin";
  if (role !== "admin" && role !== "reviewer") throw new ConfigError("RECALLOPS_DEV_ROLE must be admin or reviewer");
  const userId = env.RECALLOPS_DEV_USER_ID ?? "dev-user";
  if (!/^[\w.@-]{1,64}$/.test(userId)) throw new ConfigError("RECALLOPS_DEV_USER_ID has unsupported characters");
  return { firmId, role, userId };
}

export type SessionTokenSource = () => Promise<string | null>;

export async function consoleAuthHeaders(
  env: Env = process.env,
  sessionToken: SessionTokenSource = clerkSessionToken,
): Promise<Record<string, string>> {
  const mode = webAuthMode(env);
  if (mode === "clerk") {
    const missing = missingClerkSettings(env);
    if (missing.length) throw new ConfigError(`Clerk mode needs ${missing.join(" and ")}`);
    const token = await sessionToken();
    if (!token) throw new NotSignedIn();
    return { Authorization: `Bearer ${token}` };
  }
  if (mode === "dev") {
    const id = devIdentity(env);
    return { "X-Dev-Firm-Id": id.firmId, "X-Dev-Role": id.role, "X-Dev-User-Id": id.userId };
  }
  throw new ConfigError("console auth is not configured: set RECALLOPS_WEB_AUTH=clerk (or dev for local development)");
}
