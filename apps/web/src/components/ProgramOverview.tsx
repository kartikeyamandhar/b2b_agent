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

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-6">
      <h2 id={id} className="mb-2 text-sm font-semibold">
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
        <li key={v} className={`rounded border border-line px-1.5 py-0.5 ${mono ? "font-mono text-xs" : ""}`}>
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
        <h1 className="text-lg font-semibold">{program.title}</h1>
        <StatusBadge kind="program" value={program.status} />
        <span className="ml-auto flex flex-wrap gap-2">
          <Link
            href={`/app/programs/${program.id}/reports`}
            className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-panel"
          >
            Monthly reports
          </Link>
          <Link
            href={queueHref(program.id, { status: null, cursor: null })}
            className="rounded border border-accent px-2 py-1 text-xs font-medium text-accent hover:bg-panel"
          >
            Open claims queue
          </Link>
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-4 rounded border border-line p-4 sm:grid-cols-3 lg:grid-cols-6">
        <Fact label="Units affected">{formatCount(program.units_affected)}</Fact>
        <Fact label="Claims filed">{formatCount(m.filed)}</Fact>
        <Fact label="Awaiting review">{formatCount(m.awaitingReview)}</Fact>
        <Fact label="Waiting on claimant">{formatCount(m.needsInfo)}</Fact>
        <Fact label="Corrected">
          {formatCount(m.corrected)} <span className="text-xs text-ink-muted">({formatPercent(m.correctionRate)})</span>
        </Fact>
        <Fact label="Review above">{formatCents(program.value_threshold_cents)}</Fact>
      </dl>
      <p className="mt-2 text-xs text-ink-muted">
        Corrected counts refunds paid and repairs or replacements delivered, as a share of units affected. The monthly
        progress report is the authoritative count. Created {formatDate(program.created_at)}
        {program.opened_at ? ` · opened ${formatDate(program.opened_at)}` : " · not opened"}
        {program.closed_at ? ` · closed ${formatDate(program.closed_at)}` : ""}.
      </p>

      <Section id="claim-links" title="Claim links">
        <IssueLinks programId={program.id} open={program.status === "open"} canIssue={canAdmin} />
      </Section>

      <Section id="claims-by-status" title="Claims by status">
        <div className="overflow-x-auto rounded border border-line">
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
                    <li key={`${o.type}-${i}`} className="rounded border border-line p-2">
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
