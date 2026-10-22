// Starting Checkout or the Customer Portal: ask our API for a Stripe-hosted session and send the browser there.
// Nothing here touches payment details; the only input is which page, and the only output is a redirect. Onboarding
// (plan v3) follows the same rule for Growth: the API answers with a Checkout link, checked before the browser goes.
import { CheckoutSession, OnboardingResult, type OnboardingRequest } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { isStripeHostedUrl } from "@/lib/stripe-url";

export type StripePage = "checkout" | "portal";

function asProblem(err: unknown): ApiProblem {
  return err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
}

export async function startStripeSession(kind: StripePage, navigate: (url: string) => void): Promise<ApiProblem | null> {
  try {
    const session = await sendJson(CheckoutSession, "POST", `/v1/billing/${kind}`, undefined);
    if (!isStripeHostedUrl(session.url)) {
      return { status: 502, title: "Unexpected destination", detail: "The API did not return a Stripe page.", correlationId: null, retryAfterSeconds: null };
    }
    navigate(session.url);
    return null;
  } catch (err) {
    return asProblem(err);
  }
}

/** Where the company goes once it is set up: Recall access, to ask for its recall. */
export const AFTER_ONBOARDING = "/app/access";

export type OnboardingOutcome =
  /** The browser was sent on: to Recall access, to Stripe Checkout, or (already set up) to the console. */
  | { kind: "navigated" }
  /** Growth was chosen but no Stripe page came back: the company is set up on Free and can upgrade under Billing. */
  | { kind: "on-free" }
  | { kind: "problem"; problem: ApiProblem };

/** POST /v1/onboarding, then the next page. A full navigation, so the console layout reads the new setup state. */
export async function submitOnboarding(body: OnboardingRequest, navigate: (url: string) => void): Promise<OnboardingOutcome> {
  let result: OnboardingResult;
  try {
    result = await sendJson(OnboardingResult, "POST", "/v1/onboarding", body);
  } catch (err) {
    const problem = asProblem(err);
    if (problem.status === 409) {
      navigate("/app"); // already set up (another tab, or a second click): the console knows where to go
      return { kind: "navigated" };
    }
    return { kind: "problem", problem };
  }
  if (body.plan === "growth") {
    if (!isStripeHostedUrl(result.checkout_url)) return { kind: "on-free" };
    navigate(result.checkout_url);
    return { kind: "navigated" };
  }
  navigate(AFTER_ONBOARDING);
  return { kind: "navigated" };
}
