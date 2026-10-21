// The only external pages the UI ever sends someone to are Stripe-hosted ones (Checkout, Customer Portal, Connect
// onboarding). Their URLs come from our API; anything else is refused.
export function isStripeHostedUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && (u.hostname === "stripe.com" || u.hostname.endsWith(".stripe.com"));
  } catch {
    return false;
  }
}
