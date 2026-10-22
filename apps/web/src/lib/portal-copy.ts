// Plain sentences for portal errors: the claimant holds a link, not an account, so the console wording does not fit.
export function portalSentence(status: number): string {
  switch (status) {
    case 404:
      return "This claim link is not valid or has expired. If you started a claim and did not submit it, it may have been removed: start a new one from the product's claim link.";
    case 409:
      return "Your claim changed since this page loaded. Refresh the page to see where it stands.";
    case 413:
    case 422:
      return "Some of the information or a photo could not be accepted. Check it and try again.";
    case 429:
      return "Too many attempts in a short time. Wait a few minutes and try again.";
    case 400:
      return "Stripe needs your email address. Add it to your claim details, or choose a mailed check.";
    case 0:
      return "Your connection dropped. Nothing is lost; try again when you have signal.";
    case 503:
      return "The service is busy. Nothing is lost; try again in a minute.";
    default:
      return status >= 500 ? "Something went wrong on our side. Nothing was lost; try again." : "That did not work. Try again.";
  }
}
