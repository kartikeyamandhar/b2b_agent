import type { ReactNode } from "react";

import type { AccessStatus, OperatorAccessRequest } from "@/lib/api-types";
import { loadAccess, type Loaded } from "@/lib/api/queries";

import { AccessRequests } from "./AccessRequests";
import { CONSOLE_NAV, type NavItem } from "./AppShell";

export type AccessLoader = () => Promise<Loaded<AccessStatus>>;
export type OperatorLoader = () => Promise<Loaded<OperatorAccessRequest[]>>;
/** `gated`: the body is the access page, whatever the URL asked for, so the sidebar offers only that page. */
export type ConsoleGate = { body: ReactNode; operator: boolean; gated: boolean };

/** The sidebar while the console is gated: Recall access only. Programs, Payouts, and Billing would each open the
 *  same access page, and the one marked current would name a page the user cannot see. */
// Every /app page of a gated organization shows the access form, so its one item is current on all of them.
export const GATED_NAV: readonly NavItem[] = CONSOLE_NAV.filter((i) => i.href === "/app/access").map((i) => ({ ...i, also: ["/app"] }));

/** The sidebar items for a gate's answer: the full console unless it is gated. */
export function gateNav(gate: Pick<ConsoleGate, "gated"> | null | undefined): readonly NavItem[] {
  return gate?.gated ? GATED_NAV : CONSOLE_NAV;
}

/** Company verification (decisions log 2026-10-22): until the platform operator approves a recall for this
 *  organization, the console shows only its access requests. The operator has every access (owner decision
 *  2026-10-22): the API lets them through, so the console opens for them and the sidebar links their queue. If the
 *  status cannot be read, the page renders and shows its own error. A function the async layouts await (not a
 *  component), so the console still renders in one pass. */
export async function gateConsole(children: ReactNode, load: AccessLoader = loadAccess): Promise<ConsoleGate> {
  const access = await load();
  if (!access.ok) return { body: children, operator: false, gated: false };
  const { required, approved, operator } = access.data;
  if (!required || approved || operator) return { body: children, operator, gated: false };
  return { body: <AccessRequests status={access.data} gated />, operator, gated: true };
}
