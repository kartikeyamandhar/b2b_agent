import Link from "next/link";
import type { ReactNode } from "react";

import type { Program } from "@/lib/api-types";
import { NOT_STATED, formatCents, formatCount, formatDate, formatPercent } from "@/lib/format";
import { isClaimStatus, queueHref } from "@/lib/claims";
import { programMetrics, remedyLabel, statusRows } from "@/lib/programs";
import { statusLabel } from "@/lib/status";

import { IssueLinks } from "./IssueLinks";
import { StatusBadge } from "./StatusBadge";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

/** A headline figure: label above, a large tabular value, an optional note. */
function Kpi({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "attention" }) {
  return (
    <div className={`min-w-0 rounded-lg border bg-surface px-4 py-4 ${tone === "attention" ? "border-warn/40 border-t-2 border-t-warn" : "border-line"}`} data-kpi={label}>
      <dt className="eyebrow">{label}</dt>
      <dd className="kpi-value mt-2 [overflow-wrap:anywhere]">
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

export function ProgramOverview({ program, canAdmin = false }: { program: Program; canAdmin?: boolean }) {
  const m = programMetrics(program);
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
          <Link
            href={`/app/programs/${program.id}/settings`}
            className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-panel"
          >
            Rules and settings
          </Link>
          <Link
            href={`/app/programs/${program.id}/campaign`}
            className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-panel"
          >
            Owner notices
          </Link>
          <Link
            href={`/app/programs/${program.id}/fraud`}
            className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-panel"
          >
            Fraud rings
          </Link>
          <Link
            href={`/app/programs/${program.id}/reports`}
            className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-panel"
          >
            Monthly reports
          </Link>
          <Link
            href={queueHref(program.id, { status: null, cursor: null })}
            className="rounded-md border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:bg-panel"
          >
            Open claims queue
          </Link>
        </span>
      </div>

      {program.status === "draft" ? (
        <div className="mt-5 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink" data-draft-banner>
          <p className="font-medium">This program is a draft.</p>
          <p className="mt-1">
            Claim links cannot be issued and no claims are decided until the program is opened.{" "}
            {canAdmin ? (
              <Link href={`/app/programs/${program.id}/settings`}>Review the rules and open it</Link>
            ) : (
              "An admin reviews the rules and opens it."
            )}
          </p>
        </div>
      ) : null}

      <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
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

      <Section id="claim-links" title="Claim links">
        <IssueLinks programId={program.id} open={program.status === "open"} closed={program.status === "closed"} canIssue={canAdmin} />
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
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink-muted">{formatPercent(r.shareOfFiled)}</td>
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
                      {(o.proof_required ?? []).length > 0 ? (
                        <p className="mt-1 text-xs text-ink-muted">Proof required: {(o.proof_required ?? []).join(", ")}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </Fact>
            ) : null}
            <Fact label="Destruction instruction">{text(remedy.destruction_instruction)}</Fact>
            <Fact label="Registration">{text(remedy.registration_url)}</Fact>
            <Fact label="Contact">{text(remedy.contact)}</Fact>
          </dl>
        </Section>
      </div>
    </article>
  );
}
