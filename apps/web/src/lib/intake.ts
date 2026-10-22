// A product's public claim link (plan v3, /file/[token]): anyone with it sees the product's public facts and starts a
// claim, which gets its own private link. Server-side reads only; the client pieces (starting a claim, what this
// device remembers) live in lib/portal.ts so no client bundle pulls in the server API code.
import { IntakeView } from "@/lib/api-types";
import { intakePath } from "@/lib/portal";

import { apiJson } from "./api/backend";
import type { Env } from "./api/config";
import { load, type Loaded } from "./api/queries";

// intake.<firm>.<program>.<version> in development (auth/intake_tokens.py DEV_TOKEN); otherwise 68 signed bytes as
// unpadded base64url, always 91 characters (TOKEN_CHARS).
const UUID_PART = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const DEV_INTAKE = new RegExp(`^intake\\.${UUID_PART}\\.${UUID_PART}\\.[1-9][0-9]{0,8}$`);
const SIGNED_INTAKE = /^[A-Za-z0-9_-]{91}$/;

export function isPlausibleIntakeToken(token: string): boolean {
  return DEV_INTAKE.test(token) || SIGNED_INTAKE.test(token);
}

// A malformed link can never name a product; answer like the API does for a replaced or closed one, without asking.
const LINK_NOT_ACTIVE: Loaded<never> = {
  ok: false,
  problem: { status: 404, title: "Not Found", detail: null, correlationId: null, retryAfterSeconds: null },
};

/** What a product link shows before a claim starts, read with the link alone (no console identity). */
export function loadIntake(token: string, env: Env = process.env): Promise<Loaded<IntakeView>> {
  if (!isPlausibleIntakeToken(token)) return Promise.resolve(LINK_NOT_ACTIVE);
  return load(() => apiJson(IntakeView, { surface: "portal", path: intakePath(token) }, env));
}
