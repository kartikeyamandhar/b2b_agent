import type { ReactNode } from "react";

import type { AccessStatus, OperatorAccessRequest } from "@/lib/api-types";
import { loadAccess, loadOperatorAccess, type Loaded } from "@/lib/api/queries";

import { AccessRequests } from "./AccessRequests";
import { OperatorQueue } from "./OperatorQueue";

export type AccessLoader = () => Promise<Loaded<AccessStatus>>;
export type OperatorLoader = () => Promise<Loaded<OperatorAccessRequest[]>>;

/** Company verification (decisions log 2026-10-22): until the platform operator approves a recall for this
 *  organization, the console shows only its access requests. The operator's own organization is gated like any other
 *  (a console that answers 403 everywhere helps nobody); the operator gets the queue under their requests, so they can
 *  approve their own organization from there. If the status cannot be read, the page renders and shows its own error.
 *  A function the async layouts await (not a component), so the console still renders in one pass. */
export async function gateConsole(
  children: ReactNode,
  load: AccessLoader = loadAccess,
  loadQueue: OperatorLoader = loadOperatorAccess,
): Promise<ReactNode> {
  const access = await load();
  if (!access.ok || !access.data.required || access.data.approved) return children;
  const queue = access.data.operator ? await loadQueue() : null;
  return (
    <div className="grid gap-12">
      <AccessRequests status={access.data} gated />
      {queue?.ok ? <OperatorQueue requests={queue.data} /> : null}
    </div>
  );
}
