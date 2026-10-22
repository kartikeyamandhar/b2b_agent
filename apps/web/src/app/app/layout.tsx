import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { routeConsole } from "@/components/AccessGate";
import { AppShell } from "@/components/AppShell";
import { ClerkConsole } from "@/components/ClerkConsole";
import { devShellIdentity } from "@/lib/auth/dev-shell";
import { missingClerkSettings, webAuthMode } from "@/lib/auth/mode";
import { meOnce } from "@/lib/me";

import { Providers } from "./providers";

export const dynamic = "force-dynamic";

/** Console gate (plan v3). Clerk mode: a session (the proxy already redirects), then GET /v1/me. Development mode
 *  routes on the same answer with its settings identity: the operator goes to /operator, a company not set up sees
 *  setup, then the approval gate. */
export default async function ConsoleLayout({ children }: { children: ReactNode }) {
  const mode = webAuthMode(process.env);

  if (mode === "clerk") {
    const missing = missingClerkSettings(process.env);
    if (missing.length) {
      return <AppShell identityNote={`Console auth not configured: Clerk mode needs ${missing.join(" and ")}`} items={[]}>{null}</AppShell>;
    }
    return <ClerkConsole>{children}</ClerkConsole>;
  }

  const { note, role, userId } = devShellIdentity();
  const route = await routeConsole(children, await meOnce());
  if (route.kind === "redirect") redirect(route.to);
  return (
    <Providers key={userId ?? "unconfigured"}>
      <AppShell identityNote={note} company={route.company} role={role} items={route.nav}>
        {route.body}
      </AppShell>
    </Providers>
  );
}
