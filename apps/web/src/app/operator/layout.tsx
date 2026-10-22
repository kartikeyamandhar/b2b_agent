import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { Providers } from "@/app/app/providers";
import { routeOperator } from "@/components/AccessGate";
import { ClerkOperatorWindow } from "@/components/ClerkConsole";
import { OperatorFrame } from "@/components/ClerkShell";
import { devShellIdentity } from "@/lib/auth/dev-shell";
import { missingClerkSettings, webAuthMode } from "@/lib/auth/mode";
import { meOnce } from "@/lib/me";

export const dynamic = "force-dynamic";

/** The operator window (plan v3): standalone, with no console sidebar. GET /v1/me decides; a company account that
 *  opens /operator goes to its console. */
export default async function OperatorLayout({ children }: { children: ReactNode }) {
  if (webAuthMode(process.env) === "clerk") {
    const missing = missingClerkSettings(process.env);
    if (missing.length) {
      return <OperatorFrame email={null} note={`Console auth not configured: Clerk mode needs ${missing.join(" and ")}`}>{null}</OperatorFrame>;
    }
    return <ClerkOperatorWindow>{children}</ClerkOperatorWindow>;
  }
  const { note, userId } = devShellIdentity();
  const route = routeOperator(children, await meOnce());
  if (route.kind === "redirect") redirect(route.to);
  return (
    <Providers key={userId ?? "unconfigured"}>
      <OperatorFrame email={route.email} note={note}>
        {route.body}
      </OperatorFrame>
    </Providers>
  );
}
