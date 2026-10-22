// The console frame's identity in dev mode, shared by the /app and /operator layouts.
import { devIdentity } from "@/lib/api/auth-seam";
import { ConfigError } from "@/lib/api/config";

export type DevShellIdentity = { note: string; userId?: string; role?: "admin" | "reviewer" };

export function devShellIdentity(): DevShellIdentity {
  try {
    const id = devIdentity();
    return { note: `Development auth · firm …${id.firmId.slice(-6)} · ${id.role}`, userId: id.userId, role: id.role };
  } catch (err) {
    if (!(err instanceof ConfigError)) throw err;
    return { note: `Console auth not configured: ${err.message}` };
  }
}
