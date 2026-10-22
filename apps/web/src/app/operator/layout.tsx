import type { ReactNode } from "react";

import { Providers } from "@/app/app/providers";
import { AppShell } from "@/components/AppShell";
import { ClerkOperatorConsole } from "@/components/ClerkConsole";
import { operatorQueueOnce } from "@/lib/api/operator";
import { devShellIdentity } from "@/lib/auth/dev-shell";
import { missingClerkSettings, webAuthMode } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";

/** The operator queue's frame, outside the console's organization gate (review of the operator-by-email change): signed
 *  in without an organization, or in one where the role has no console access, the operator still reaches it. */
export default async function OperatorLayout({ children }: { children: ReactNode }) {
  if (webAuthMode(process.env) === "clerk") {
    const missing = missingClerkSettings(process.env);
    if (missing.length) {
      return <AppShell identityNote={`Console auth not configured: Clerk mode needs ${missing.join(" and ")}`}>{null}</AppShell>;
    }
    const queue = await operatorQueueOnce();
    return <ClerkOperatorConsole operator={queue.ok}>{children}</ClerkOperatorConsole>;
  }
  const { note, org, role } = devShellIdentity();
  const queue = await operatorQueueOnce();
  return (
    <Providers>
      <AppShell identityNote={note} org={org} role={role} operator={queue.ok} items={queue.ok ? undefined : []}>
        {children}
      </AppShell>
    </Providers>
  );
}
