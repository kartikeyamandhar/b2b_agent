import type { ReactNode } from "react";

import { Onboarding } from "@/app/app/welcome/Onboarding";
import type { AccessStatus, Me, MeFirm, PlanInfo } from "@/lib/api-types";
import { isSignInUnavailable, type ApiProblem } from "@/lib/api/problem";
import { loadAccess, type Loaded } from "@/lib/api/queries";
import { accountHome, companyIdentity, plansOnce, type CompanyIdentity } from "@/lib/me";

import { AccessRequests } from "./AccessRequests";
import { CONSOLE_NAV, type NavItem } from "./AppShell";
import { PathGate } from "./NavLinks";
import { ProblemAlert } from "./ProblemAlert";
import { SignInUnavailable } from "./States";

export type AccessLoader = () => Promise<Loaded<AccessStatus>>;
export type PlansLoader = () => Promise<Loaded<PlanInfo[]>>;
/** `gated`: every /app page but the allowed ones shows the access page, so the sidebar offers only those. */
export type ConsoleGate = { body: ReactNode; gated: boolean };

/** What an unapproved company still reaches (plan v3): Recall access, and Billing with its company settings. */
export const GATED_PATHS: readonly string[] = ["/app/access", "/app/billing"];

/** What a company that has not finished setup still reaches besides setup itself: the Checkout return page. */
export const ONBOARDING_PATHS: readonly string[] = ["/app/billing/return"];

/** The sidebar while the console is gated: Billing and Recall access. Every other /app page shows the access form,
 *  so Recall access stands for those paths too. */
export const GATED_NAV: readonly NavItem[] = CONSOLE_NAV.filter((i) => GATED_PATHS.includes(i.href)).map((i) =>
  i.href === "/app/access" ? { ...i, also: ["/app"] } : i,
);

/** The sidebar items for a gate's answer: the full console unless it is gated. */
export function gateNav(gate: Pick<ConsoleGate, "gated"> | null | undefined): readonly NavItem[] {
  return gate?.gated ? GATED_NAV : CONSOLE_NAV;
}

/** The access page for a company: its requests and the way to ask, or the error that kept them from loading. */
export function accessScreen(access: Loaded<AccessStatus>, firm: MeFirm | null, gated: boolean): ReactNode {
  return access.ok ? <AccessRequests status={access.data} gated={gated} firm={firm} /> : <ProblemAlert problem={access.problem} />;
}

/** The setup page, with the plans the API offers (or a note when they could not be read). */
export function welcomeBody(plans: Loaded<PlanInfo[]>): ReactNode {
  return <Onboarding plans={plans.ok ? plans.data : []} plansProblem={plans.ok ? null : plans.problem} />;
}

/** Company verification (decisions log 2026-10-22): until the platform operator approves a recall for this company,
 *  the console shows its access requests in place of every page but Recall access and Billing. If the status cannot
 *  be read, the page renders and shows its own error (the API refuses unapproved companies with a 403 either way). A
 *  function the async layouts await (not a component), so the console still renders in one pass. */
export async function gateConsole(children: ReactNode, load: AccessLoader = loadAccess, firm: MeFirm | null = null): Promise<ConsoleGate> {
  const access = await load();
  if (!access.ok) return { body: children, gated: false };
  const { required, approved } = access.data;
  if (!required || approved) return { body: children, gated: false };
  const fallback = accessScreen(access, firm, true);
  return { body: <PathGate allow={GATED_PATHS} fallback={fallback}>{children}</PathGate>, gated: true };
}

/** GET /v1/me failed: a short retry state while sign-in verification is down, the API's error otherwise. */
export function AccountProblem({ problem, retryHref }: { problem: ApiProblem; retryHref: string }) {
  return isSignInUnavailable(problem) ? <SignInUnavailable retryHref={retryHref} /> : <ProblemAlert problem={problem} />;
}

const NO_COMPANY: ApiProblem = { status: 403, title: "Forbidden", detail: "this account has no company yet; sign out and sign in again", correlationId: null, retryAfterSeconds: null };

export type ConsoleRoute =
  | { kind: "redirect"; to: string }
  | { kind: "console"; body: ReactNode; nav: readonly NavItem[]; company?: CompanyIdentity };

/**
 * The console's routing on GET /v1/me (plan v3), shared by the Clerk and development layouts: the operator goes to
 * their window; a company that has not finished setup sees the setup page in place of the body; then the approval
 * gate. Fails closed: when /v1/me cannot be read, no console page renders.
 */
export async function routeConsole(
  children: ReactNode,
  me: Loaded<Me>,
  deps: { access?: AccessLoader; plans?: PlansLoader } = {},
): Promise<ConsoleRoute> {
  if (!me.ok) return { kind: "console", body: <AccountProblem problem={me.problem} retryHref="/app" />, nav: [] };
  const home = accountHome(me.data);
  if (home === "/operator") return { kind: "redirect", to: home };
  const firm = me.data.firm;
  if (!firm) return { kind: "console", body: <ProblemAlert problem={NO_COMPANY} />, nav: [] };
  if (home === "/app/welcome") {
    const fallback = welcomeBody(await (deps.plans ?? plansOnce)());
    return { kind: "console", body: <PathGate allow={ONBOARDING_PATHS} fallback={fallback}>{children}</PathGate>, nav: [] };
  }
  // GET /v1/me already says whether the company is approved, so an approved company skips the access read.
  const gate = firm.approved ? { body: children, gated: false } : await gateConsole(children, deps.access, firm);
  return { kind: "console", body: gate.body, nav: gateNav(gate), company: companyIdentity(firm, me.data.email) };
}

export type OperatorRoute = { kind: "redirect"; to: string } | { kind: "window"; email: string | null; body: ReactNode };

/** The operator window's routing on GET /v1/me: the operator stays; any company account goes to its console. */
export function routeOperator(children: ReactNode, me: Loaded<Me>): OperatorRoute {
  if (!me.ok) return { kind: "window", email: null, body: <AccountProblem problem={me.problem} retryHref="/operator" /> };
  if (!me.data.operator) return { kind: "redirect", to: "/app" };
  return { kind: "window", email: me.data.email, body: children };
}
