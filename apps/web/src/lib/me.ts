// Who is signed in (GET /v1/me, plan v3): the platform operator, or one company with its plan and setup state. One
// account is one company (no organizations); the console routes on this answer, never on Clerk organization roles.
import { Me, PlanInfo, type MeFirm } from "@/lib/api-types";
import { cache } from "react";
import { z } from "zod";

import { apiJson } from "./api/backend";
import type { Env } from "./api/config";
import { load, type Loaded } from "./api/queries";
import { planLabel } from "./billing";

export function loadMe(env: Env = process.env): Promise<Loaded<Me>> {
  return load(() => apiJson(Me, { surface: "console", path: "/v1/me" }, env));
}

/** Loaded once per request: the console layout and the page it frames share one call. */
export const meOnce = cache((): Promise<Loaded<Me>> => loadMe());

/** What each plan allows and costs, from the API's settings (prices are never hardcoded in the website). */
export function loadPlans(env: Env = process.env): Promise<Loaded<PlanInfo[]>> {
  // Public: no console identity, so the homepage and sign-up can show it too.
  return load(() => apiJson(z.array(PlanInfo), { surface: "portal", path: "/v1/plans" }, env));
}

/** Loaded once per request: the setup page and the layout that shows it in place of other pages share one call. */
export const plansOnce = cache((): Promise<Loaded<PlanInfo[]>> => loadPlans());

/** Where a signed-in account belongs before any console page renders: the operator's window, the company's setup
 *  page, or null (the console itself). */
export function accountHome(me: Me): "/operator" | "/app/welcome" | null {
  if (me.operator) return "/operator";
  if (me.firm && !me.firm.onboarded) return "/app/welcome";
  return null;
}

/** Where /app sends an account: its home, then Recall access until the operator approves the company, then its
 *  products. */
export function consoleStart(me: Me): string {
  const home = accountHome(me);
  if (home) return home;
  return me.firm && !me.firm.approved ? "/app/access" : "/app/programs";
}

export type CompanyIdentity = { name: string; detail: string };

/** The company at the foot of the sidebar: its name, and the plan it has now (effective_plan). */
export function companyIdentity(firm: Pick<MeFirm, "name" | "effective_plan">, email?: string | null): CompanyIdentity {
  const plan = `${planLabel(firm.effective_plan)} plan`;
  return { name: firm.name, detail: email ? `${plan} · ${email}` : plan };
}
