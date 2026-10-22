// The console frame's identity in dev mode, shared by the /app and /operator layouts.
import { devIdentity } from "@/lib/api/auth-seam";
import { ConfigError } from "@/lib/api/config";

export type DevShellIdentity = { note: string; org?: { name: string; detail: string }; role?: "admin" | "reviewer" };

export function devShellIdentity(): DevShellIdentity {
  try {
    const id = devIdentity();
    return {
      note: `Development auth · firm ${id.firmId} · ${id.role}`,
      org: { name: "Development firm", detail: `…${id.firmId.slice(-6)} · ${id.role === "admin" ? "Admin" : "Reviewer"} · dev auth` },
      role: id.role,
    };
  } catch (err) {
    if (!(err instanceof ConfigError)) throw err;
    return { note: `Console auth not configured: ${err.message}` };
  }
}
