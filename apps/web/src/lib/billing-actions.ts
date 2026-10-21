// Starting Checkout or the Customer Portal: ask our API for a Stripe-hosted session and send the browser there.
// Nothing here touches payment details; the only input is which page, and the only output is a redirect.
import { CheckoutSession } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { isStripeHostedUrl } from "@/lib/stripe-url";

export type StripePage = "checkout" | "portal";

export async function startStripeSession(kind: StripePage, navigate: (url: string) => void): Promise<ApiProblem | null> {
  try {
    const session = await sendJson(CheckoutSession, "POST", `/v1/billing/${kind}`, undefined);
    if (!isStripeHostedUrl(session.url)) {
      return { status: 502, title: "Unexpected destination", detail: "The API did not return a Stripe page.", correlationId: null, retryAfterSeconds: null };
    }
    navigate(session.url);
    return null;
  } catch (err) {
    return err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
  }
}
