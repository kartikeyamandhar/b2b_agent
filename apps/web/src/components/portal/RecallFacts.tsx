// What every claimant page says about the recall itself: who runs it, which recall it is, and how to reach them.
// No hooks, so server pages and client components both render these.

/** The page title with the company running the recall above it (plan v3: the claimant deals with that company). */
export function ClaimHeader({ companyName, title, recallNumber }: { companyName?: string | null; title: string; recallNumber?: string | null }) {
  const company = companyName?.trim();
  return (
    <header className="mb-6 grid gap-1.5">
      {company ? (
        <p className="text-sm text-ink-muted" data-company>
          <span className="font-medium text-ink">{company}</span> is running this recall.
        </p>
      ) : null}
      <h1 className="display-title text-[1.375rem] leading-snug sm:text-[1.75rem]">{title}</h1>
      {recallNumber ? (
        <p className="text-sm text-ink-muted" data-recall-number>
          Recall number {recallNumber}
        </p>
      ) : null}
    </header>
  );
}

/** The notice's contact sentence, as written (it names the company's phone, email, or website itself). */
export function RecallContact({ contact }: { contact?: string | null }) {
  const text = contact?.trim();
  if (!text) return null;
  return (
    <section aria-labelledby="recall-contact-title" className="mt-6 grid gap-1 border-t border-line pt-4 text-sm" data-recall-contact>
      <h2 id="recall-contact-title" className="font-semibold text-ink">
        Questions about this recall
      </h2>
      <p className="whitespace-pre-wrap text-ink-muted [overflow-wrap:anywhere]">{text}</p>
    </section>
  );
}

/** A product link that no longer starts claims: replaced, closed, or never valid (the API answers 404 for all three,
 *  so the page cannot say which, or name the company). Calm, not an error box: nothing went wrong on the device. */
export function LinkNotActive({ heading = "h1" }: { heading?: "h1" | "h2" }) {
  const Heading = heading;
  return (
    <section aria-labelledby="link-inactive-title" className="rounded-xl border border-line bg-surface p-5" data-link-inactive>
      <Heading id="link-inactive-title" className="display-title text-2xl">
        This claim link is not active
      </Heading>
      <p className="mt-1 text-base">
        The company may have replaced it with a new link or closed it. Use the newest link the company shared, or contact them.
      </p>
    </section>
  );
}
