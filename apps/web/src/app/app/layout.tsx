import type { ReactNode } from "react";

import { gateConsole, gateNav } from "@/components/AccessGate";
import { AppShell } from "@/components/AppShell";
import { ClerkConsole } from "@/components/ClerkConsole";
import { devShellIdentity } from "@/lib/auth/dev-shell";
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

  const { note, org, role } = devShellIdentity();
  const gate = await gateConsole(children);
  return (
    <Providers>
      <AppShell identityNote={note} org={org} role={role} operator={gate.operator} items={gateNav(gate)}>
        {gate.body}
      </AppShell>
    </Providers>
  );
}
