import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { Providers } from "@/app/app/providers";
import { clerkContext } from "@/lib/auth/clerk-session";
import { consoleAccess, type ClerkContext } from "@/lib/auth/mode";

import { gateConsole, gateNav, type AccessLoader } from "./AccessGate";
import { AccountControls } from "./AccountControls";
import { AppShell, CONSOLE_NAV, OPERATOR_NAV } from "./AppShell";
import { ClerkShell } from "./ClerkShell";
import { OperatorLink } from "./OperatorLink";

const ROLE_LABEL = { admin: "Admin", reviewer: "Reviewer" } as const;

function NoAccess({ reason }: { reason: string }) {
  return (
    <div role="alert" className="mx-auto mt-10 max-w-md rounded border border-warn/50 bg-warn-soft p-4 text-sm text-warn-ink">
      <p className="font-medium">No access to this organization</p>
      <p className="mt-1">{reason}</p>
    </div>
  );
}

/** The console in Clerk mode: the access decision from consoleAccess(), then the shell with account controls. A user
 *  without an organization goes to the organization page at once; the platform operator's queue is /operator, outside
 *  this gate, linked from the pages such a user sees. */
export async function ClerkConsole({
  children,
  context = clerkContext,
  recallAccess,
  operatorCheck,
}: {
  children: ReactNode;
  context?: () => Promise<ClerkContext>;
  recallAccess?: AccessLoader;
  operatorCheck?: () => Promise<boolean>;
}) {
  const access = consoleAccess(await context());
  if (access.kind === "redirect") redirect(access.to);
  const gate = access.kind === "ok" ? await gateConsole(children, recallAccess) : null;
  return (
    <ClerkShell>
      {access.kind === "no-access" ? (
        <AppShell identityNote="Signed in" controls={<AccountControls />}>
          <NoAccess reason={access.reason} />
          <div className="mx-auto mt-4 max-w-md">
            <Suspense fallback={null}>
              <OperatorLink check={operatorCheck} />
            </Suspense>
          </div>
        </AppShell>
      ) : (
        // A new QueryClient per organization: switching organizations never shows another firm's cached data.
        <Providers key={access.orgId}>
          <AppShell identityNote={ROLE_LABEL[access.role]} controls={<AccountControls />} role={access.role} operator={gate?.operator} items={gateNav(gate)}>
            {gate?.body}
          </AppShell>
        </Providers>
      )}
    </ClerkShell>
  );
}

/** The /operator frame in Clerk mode: no organization gate. With a console role in the active organization, the full
 *  console navigation shows; otherwise only the queue, and a note on running a recall yourself. */
export async function ClerkOperatorConsole({
  children,
  operator,
  context = clerkContext,
}: {
  children: ReactNode;
  operator: boolean;
  context?: () => Promise<ClerkContext>;
}) {
  const ctx = await context();
  if (!ctx.userId) redirect("/sign-in");
  const access = consoleAccess(ctx);
  return (
    <ClerkShell>
      <Providers key={access.kind === "ok" ? access.orgId : "no-organization"}>
        <AppShell
          identityNote={access.kind === "ok" ? ROLE_LABEL[access.role] : operator ? "Platform operator" : "Signed in"}
          controls={<AccountControls />}
          role={access.kind === "ok" ? access.role : undefined}
          operator={operator}
          items={!operator ? [] : access.kind === "ok" ? CONSOLE_NAV : OPERATOR_NAV}
        >
          {access.kind !== "ok" && operator ? (
            <p className="mb-6 w-full max-w-4xl text-sm text-ink-muted" data-operator-no-org>
              You are signed in without an organization the console can open.{" "}
              <Link href="/organization" className="text-accent hover:underline">
                Create or choose one
              </Link>{" "}
              to run a recall yourself.
            </p>
          ) : null}
          {children}
        </AppShell>
      </Providers>
    </ClerkShell>
  );
}
