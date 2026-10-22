import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { Providers } from "@/app/app/providers";
import { clerkContext } from "@/lib/auth/clerk-session";
import { consoleAccess, type ClerkContext } from "@/lib/auth/mode";

import { gateConsole, type AccessLoader } from "./AccessGate";
import { AccountControls } from "./AccountControls";
import { AppShell } from "./AppShell";
import { ClerkShell } from "./ClerkShell";

const ROLE_LABEL = { admin: "Admin", reviewer: "Reviewer" } as const;

function NoAccess({ reason }: { reason: string }) {
  return (
    <div role="alert" className="mx-auto mt-10 max-w-md rounded border border-warn/50 bg-warn-soft p-4 text-sm text-warn-ink">
      <p className="font-medium">No access to this organization</p>
      <p className="mt-1">{reason}</p>
    </div>
  );
}

/** The console in Clerk mode: the access decision from consoleAccess(), then the shell with account controls. */
export async function ClerkConsole({
  children,
  context = clerkContext,
  recallAccess,
}: {
  children: ReactNode;
  context?: () => Promise<ClerkContext>;
  recallAccess?: AccessLoader;
}) {
  const access = consoleAccess(await context());
  if (access.kind === "redirect") redirect(access.to);
  const body = access.kind === "ok" ? await gateConsole(children, recallAccess) : null;
  return (
    <ClerkShell>
      {access.kind === "no-access" ? (
        <AppShell identityNote="Signed in" controls={<AccountControls />}>
          <NoAccess reason={access.reason} />
        </AppShell>
      ) : (
        // A new QueryClient per organization: switching organizations never shows another firm's cached data.
        <Providers key={access.orgId}>
          <AppShell identityNote={ROLE_LABEL[access.role]} controls={<AccountControls />} role={access.role}>
            {body}
          </AppShell>
        </Providers>
      )}
    </ClerkShell>
  );
}
