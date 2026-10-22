// The platform operator's window for server components (plan v3: the operator has no company and never sees the
// console). The /operator layout routes on GET /v1/me; the API decides who the operator is on every call too.
import { cache } from "react";

import type { OperatorAccessRequest } from "@/lib/api-types";

import { loadOperatorAccess, type Loaded } from "./queries";

/** Loaded once per request by the /operator page. */
export const operatorQueueOnce = cache((): Promise<Loaded<OperatorAccessRequest[]>> => loadOperatorAccess());
