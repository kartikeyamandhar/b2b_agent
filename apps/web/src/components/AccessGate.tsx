import type { ReactNode } from "react";

import type { AccessStatus } from "@/lib/api-types";
import { loadAccess, type Loaded } from "@/lib/api/queries";

import { AccessRequests } from "./AccessRequests";

export type AccessLoader = () => Promise<Loaded<AccessStatus>>;

/** Company verification (decisions log 2026-10-22): until the platform operator approves a recall for this
 *  organization, the console shows only its access requests. The operator always passes, so the operator queue works
 *  before their own organization is approved. If the status cannot be read, the page renders and shows its own error.
 *  A function the async layouts await (not a component), so the console still renders in one pass. */
export async function gateConsole(children: ReactNode, load: AccessLoader = loadAccess): Promise<ReactNode> {
  const access = await load();
  if (!access.ok || !access.data.required || access.data.approved || access.data.operator) return children;
  return <AccessRequests status={access.data} gated />;
}
