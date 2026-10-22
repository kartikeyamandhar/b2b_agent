import Link from "next/link";
import type { ReactNode } from "react";

import type { Program } from "@/lib/api-types";
import { NOT_STATED, formatCents, formatCount, formatDate, formatPercent, formatPercentFixed } from "@/lib/format";
import { isClaimStatus, queueHref } from "@/lib/claims";
import { instructionReadsAsSentence } from "@/lib/portal";
import { linkState } from "@/lib/products";
import { programMetrics, remedyLabel, statusRows } from "@/lib/programs";
import { proofSentence, statusLabel } from "@/lib/status";

import { ProductLinkCard } from "./ProductLinkCard";
import { RulesCheckReminder } from "./RulesCheckNote";
import { StatusBadge } from "./StatusBadge";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

/**
 * A headline figure: label above, a large tabular value, an optional note. The value sits at the foot of the tile, so
 * a row's values line up when one label wraps; the attention accent is an inset line, so it adds no height.
 */
function Kpi({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "attention" }) {
  return (
    <div
      className={`flex min-w-0 flex-col rounded-lg border bg-surface px-4 py-4 xl:px-3 ${tone === "attention" ? "border-warn/40 border-t-warn shadow-[inset_0_1px_0_0_var(--warn)]" : "border-line"}`}
      data-kpi={label}
    >
      <dt className="eyebrow">{label}</dt>
      <dd className="kpi-value mt-auto break-words pt-2">
        {value}
        {note ? <span className="ml-1.5 align-middle text-sm font-normal text-ink-muted">{note}</span> : null}
      </dd>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-10">
      <h2 id={id} className="section-title mb-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

function List({ items, mono = false }: { items: readonly string[]; mono?: boolean }) {
  if (items.length === 0) return <span className="text-ink-muted">{NOT_STATED}</span>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((v) => (
        <li key={v} className={`rounded border border-line bg-surface px-1.5 py-0.5 ${mono ? "font-mono text-xs" : ""}`}>
          {v}
        </li>
      ))}
    </ul>
  );
}

const text = (v: string | null | undefined) => (v ? v : <span className="text-ink-muted">{NOT_STATED}</span>);

/** One radius and border for every action in the header; the claims queue is the primary one, as on /app/programs. */
const HEADER_LINK = "rounded-md border border-line-strong bg-surface px-2.5 py-1 text-xs font-medium whitespace-nowrap text-ink hover:border-ink";
const HEADER_PRIMARY = "rounded-md border border-accent bg-accent px-2.5 py-1 text-xs font-medium whitespace-nowrap text-bg hover:opacity-90";

/**
 * The stored destruction instruction is the extractor's span of the notice, which can start inside a URL
 * ("com/3M/en_US/..."). It is shown as stored, with a note, when it does not read as a sentence: it starts in
 * lowercase, or it carries a path or a web address.
 */
export function instructionLooksCut(instruction: string): boolean {
  return !instructionReadsAsSentence(instruction); // the portal's own test, so the console and the portal agree
}

/** Only an https address becomes a link; anything else stays text (the notice may write "www.example.com/recall"). */
export function registrationHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url.trim());
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

const BANNER = "mt-5 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink";

/** What keeps the product from taking claims, at the top of its page; nothing when it takes them (or is closed). */
export function ProductBanner({ program }: { program: Program }) {
  const state = linkState(program);
  const settings = `/app/programs/${program.id}/settings`;
  if (state.kind === "paused") {
    return (
      <div className={BANNER} data-paused-banner>
        <p>
          <span className="font-medium">Paused.</span> Your plan covers one product. Upgrade under <Link href="/app/billing">Billing</Link> to take
          claims here again.
        </p>
      </div>
    );
  }
  if (state.kind === "needs_amount") {
    return (
      <div className={BANNER} data-refund-amount-banner>
        <p className="font-medium">Set the refund amount</p>
        <p className="mt-1">
          This product pays refunds, and the recall notice states no price, so it cannot take claims until the price range
          is set. <Link href={`${settings}#identification-rules`}>Set the price range in Rules and settings</Link>, then
          open the product.
        </p>
      </div>
    );
  }
  if (state.kind === "draft") {
    return (
      <div className={BANNER} data-draft-banner>
        <p className="font-medium">This product is a draft.</p>
        <p className="mt-1">
          It takes no claims until it opens. <Link href={settings}>Review the rules and open it</Link>.
        </p>
      </div>
    );
  }
  return null;
}

const NO_LINK: Record<string, string> = {
  closed: "This product is closed. Its claim link, and every claimant’s private link, no longer work.",
  paused: "The claim link is paused while your plan covers another product. Claims already started carry on.",
  needs_amount: "No claim link yet. It appears once the refund amount is set and the product opens.",
  draft: "No claim link yet. It appears once the product opens.",
  none: "No claim link right now.",
};

export function ProgramOverview({ program, canAdmin = false, companyName = null }: { program: Program; canAdmin?: boolean; companyName?: string | null }) {
  const m = programMetrics(program);
  const link = linkState(program);
  const ident = program.identification_rules;
  const remedy = program.remedy_rules;
  const price = ident.price_range_cents;
  const soldWindow =
    ident.sold_from || ident.sold_to ? `${formatDate(ident.sold_from)} to ${formatDate(ident.sold_to)}` : null;

  return (
    <article>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="page-title">{program.title}</h1>
        <StatusBadge kind="program" value={program.status} />
        <span className="ml-auto flex flex-wrap gap-2">
          <Link href={`/app/programs/${program.id}/settings`} className={HEADER_LINK}>
            Rules and settings
          </Link>
          <Link href={`/app/programs/${program.id}/campaign`} className={HEADER_LINK}>
            Owner notices
          </Link>
          <Link href={`/app/programs/${program.id}/fraud`} className={HEADER_LINK}>
            Fraud rings
          </Link>
          <Link href={`/app/programs/${program.id}/reports`} className={HEADER_LINK}>
            Monthly reports
          </Link>
          <Link href={queueHref(program.id, { status: null, cursor: null })} className={HEADER_PRIMARY}>
            Open claims queue
          </Link>
        </span>
      </div>

      <ProductBanner program={program} />
      {program.status === "open" ? (
        // Empty until this browser's memory is read; the note never leaves a gap when it is not shown.
        <div className="mt-5 empty:hidden">
          <RulesCheckReminder programId={program.id} />
        </div>
      ) : null}

      {/* Six across only from xl: at 1024 to 1279 px a sixth of the column is too narrow for a five-digit value. */}
      <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Units affected" value={formatCount(program.units_affected)} />
        <Kpi label="Claims filed" value={formatCount(m.filed)} />
        <Kpi label="Awaiting review" value={formatCount(m.awaitingReview)} tone={m.awaitingReview > 0 ? "attention" : undefined} />
        <Kpi label="Waiting on claimant" value={formatCount(m.needsInfo)} />
        <Kpi label="Corrected" value={formatCount(m.corrected)} note={formatPercent(m.correctionRate)} />
        <Kpi label="Review above" value={formatCents(program.value_threshold_cents)} />
      </dl>
      <p className="mt-2 text-xs text-ink-muted">
        Corrected counts refunds paid and repairs or replacements delivered, as a share of units affected. The monthly
        progress report is the authoritative count. Created {formatDate(program.created_at)}
        {program.opened_at ? ` · opened ${formatDate(program.opened_at)}` : " · not opened"}
        {program.closed_at ? ` · closed ${formatDate(program.closed_at)}` : ""}.
      </p>

      <Section id="claim-link" title="Claim link">
        {link.kind === "live" ? (
          <ProductLinkCard program={program} url={link.url} companyName={companyName} canAdmin={canAdmin} />
        ) : (
          <p className="rounded-lg border border-line bg-surface p-4 text-sm text-ink-muted" data-no-claim-link={link.kind}>
            {NO_LINK[link.kind]}
          </p>
        )}
      </Section>

      <Section id="claims-by-status" title="Claims by status">
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Claims by status</caption>
            <thead className="border-b border-line bg-panel">
              <tr>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-muted">Status</th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium text-ink-muted">Claims</th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium text-ink-muted">Share of filed</th>
              </tr>
            </thead>
            <tbody>
              {statusRows(program.claim_counts).map((r) => (
                <tr key={r.status} className="border-b border-line last:border-b-0" data-status-row={r.status}>
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {isClaimStatus(r.status) ? (
                      <Link href={queueHref(program.id, { status: r.status, cursor: null })} aria-label={`Show ${statusLabel("claim", r.status).label} claims`}>
                        <StatusBadge kind="claim" value={r.status} />
                      </Link>
                    ) : (
                      <StatusBadge kind="claim" value={r.status} />
                    )}
                  </th>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(r.count)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink-muted">{formatPercentFixed(r.shareOfFiled)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="grid gap-x-8 lg:grid-cols-2">
        <Section id="identification" title="Identification rules">
          <dl className="grid gap-3">
            <Fact label="Models">
              <List items={ident.models ?? []} mono />
            </Fact>
            <Fact label="Where the label is">{text(ident.model_label_location)}</Fact>
            <Fact label="Sold at">
              <List items={ident.sold_at ?? []} />
            </Fact>
            <Fact label="Sold">{text(soldWindow)}</Fact>
            <Fact label="Price">{price ? `${formatCents(price.min_cents)} to ${formatCents(price.max_cents)}` : text(null)}</Fact>
            <Fact label="Serial ranges">
              <List items={(ident.serial_ranges ?? []).map((r) => r.raw)} mono />
            </Fact>
            <Fact label="Date codes">
              <List items={(ident.date_codes ?? []).map((d) => d.raw)} mono />
            </Fact>
          </dl>
        </Section>

        <Section id="remedy" title="Remedy">
          <dl className="grid gap-3">
            <Fact label="Remedy">{remedyLabel(remedy.remedy_type)}</Fact>
            {(remedy.options ?? []).length > 0 ? (
              <Fact label="Options">
                <ul className="grid gap-2">
                  {(remedy.options ?? []).map((o, i) => (
                    <li key={`${o.type}-${i}`} className="rounded-lg border border-line bg-surface p-2">
                      <p className="font-medium">{remedyLabel(o.type)}</p>
                      {(o.steps ?? []).length > 0 ? (
                        <ol className="mt-1 list-decimal pl-5 text-ink-muted">
                          {(o.steps ?? []).map((s) => (
                            <li key={s}>{s}</li>
                          ))}
                        </ol>
                      ) : null}
                      {proofSentence(o.proof_required ?? []) ? <p className="mt-1 text-xs text-ink-muted">{proofSentence(o.proof_required ?? [])}</p> : null}
                    </li>
                  ))}
                </ul>
              </Fact>
            ) : null}
            <Fact label="Destruction instruction">
              {text(remedy.destruction_instruction)}
              {remedy.destruction_instruction && instructionLooksCut(remedy.destruction_instruction) ? (
                <span className="mt-1 block text-xs text-ink-muted" data-instruction-cut>
                  This looks cut off from the notice. It can be corrected in{" "}
                  <Link href={`/app/programs/${program.id}/settings`}>Rules and settings</Link>.
                </span>
              ) : null}
            </Fact>
            <Fact label="Registration">
              {registrationHref(remedy.registration_url) ? (
                <a href={registrationHref(remedy.registration_url)!} target="_blank" rel="noopener noreferrer" className="[overflow-wrap:anywhere]">
                  {remedy.registration_url}
                </a>
              ) : (
                <span className="[overflow-wrap:anywhere]">{text(remedy.registration_url)}</span>
              )}
            </Fact>
            <Fact label="Contact">{text(remedy.contact)}</Fact>
          </dl>
        </Section>
      </div>
    </article>
  );
}
