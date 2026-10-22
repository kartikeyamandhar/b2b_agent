import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { Providers } from "@/app/app/providers";
import type { Me } from "@/lib/api-types";
import type { Loaded } from "@/lib/api/queries";
import { clerkContext } from "@/lib/auth/clerk-session";
import { consoleAccess, type ClerkContext } from "@/lib/auth/mode";
import { meOnce } from "@/lib/me";

import { routeConsole, routeOperator, type AccessLoader, type PlansLoader } from "./AccessGate";
import { AccountControls, SignOutControl } from "./AccountControls";
import { AppShell } from "./AppShell";
import { ClerkShell, OperatorFrame } from "./ClerkShell";

export type MeLoader = () => Promise<Loaded<Me>>;

/** The console in Clerk mode (plan v3): a session, then GET /v1/me decides. The operator goes to their window; a
 *  company sees setup, then its approval gate, then its console. Every signed-in company account is its admin. */
export async function ClerkConsole({
  children,
  context = clerkContext,
  me = meOnce,
  recallAccess,
  plans,
}: {
  children: ReactNode;
  context?: () => Promise<ClerkContext>;
  me?: MeLoader;
  recallAccess?: AccessLoader;
  plans?: PlansLoader;
}) {
  const access = consoleAccess(await context());
  if (access.kind === "redirect") redirect(access.to);
  const route = await routeConsole(children, await me(), { access: recallAccess, plans });
  if (route.kind === "redirect") redirect(route.to);
  return (
    <ClerkShell>
      {/* A new QueryClient per account: signing in as another account never shows the last one's cached data. */}
      <Providers key={access.userId}>
        <AppShell identityNote="Signed in" controls={<AccountControls />} company={route.company} role="admin" items={route.nav}>
          {route.body}
        </AppShell>
      </Providers>
    </ClerkShell>
  );
}

/** The operator window in Clerk mode: a session, then GET /v1/me. Anyone but the operator goes to their console. */
export async function ClerkOperatorWindow({ children, context = clerkContext, me = meOnce }: { children: ReactNode; context?: () => Promise<ClerkContext>; me?: MeLoader }) {
  const access = consoleAccess(await context());
  if (access.kind === "redirect") redirect(access.to);
  const route = routeOperator(children, await me());
  if (route.kind === "redirect") redirect(route.to);
  return (
    <ClerkShell>
      <Providers key={access.userId}>
        <OperatorFrame email={route.email} controls={<SignOutControl />}>
          {route.body}
        </OperatorFrame>
      </Providers>
    </ClerkShell>
  );
}
