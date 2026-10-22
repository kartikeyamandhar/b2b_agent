// The platform operator's queue for server components (owner decision 2026-10-22: the operator by verified email,
// with every access). The /operator page lives outside the console's organization gate, so no organization state
// can hide it; the API decides who the operator is on every call.
import { cache } from "react";

import { loadOperatorAccess, type Loaded } from "./queries";
import type { OperatorAccessRequest } from "@/lib/api-types";

/** Loaded once per request: the /operator layout (for the sidebar) and its page share one call. */
export const operatorQueueOnce = cache((): Promise<Loaded<OperatorAccessRequest[]>> => loadOperatorAccess());

/** A quick answer for pages without a console (no organization, or a role without console access): a 3 s deadline,
 *  and any failure reads as not the operator, so those pages never wait on a sleeping API. */
export async function isOperatorQuick(): Promise<boolean> {
  try {
    return (await loadOperatorAccess(process.env, 3000)).ok;
  } catch {
    return false;
  }
}
