import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { ClerkConsole } from "@/components/ClerkConsole";
import { devIdentity } from "@/lib/api/auth-seam";
import { ConfigError } from "@/lib/api/config";
import { missingClerkSettings, webAuthMode } from "@/lib/auth/mode";

import { Providers } from "./providers";

export const dynamic = "force-dynamic";

/** Console gate. Clerk mode: a session (the proxy already redirects), an active organization, and a role the backend accepts. */
export default async function ConsoleLayout({ children }: { children: ReactNode }) {
  const mode = webAuthMode(process.env);

  if (mode === "clerk") {
    const missing = missingClerkSettings(process.env);
    if (missing.length) {
      return <AppShell identityNote={`Console auth not configured: Clerk mode needs ${missing.join(" and ")}`}>{null}</AppShell>;
    }
    return <ClerkConsole>{children}</ClerkConsole>;
  }

  let note: string;
  let org: { name: string; detail: string } | undefined;
  try {
    const id = devIdentity();
    note = `Development auth · firm ${id.firmId} · ${id.role}`;
    org = { name: "Development firm", detail: `…${id.firmId.slice(-6)} · ${id.role === "admin" ? "Admin" : "Reviewer"} · dev auth` };
  } catch (err) {
    if (!(err instanceof ConfigError)) throw err;
    note = `Console auth not configured: ${err.message}`;
  }
  return (
    <Providers>
      <AppShell identityNote={note} org={org}>
        {children}
      </AppShell>
    </Providers>
  );
}
