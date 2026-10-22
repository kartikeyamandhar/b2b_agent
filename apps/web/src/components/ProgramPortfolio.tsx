import Link from "next/link";

import type { Program } from "@/lib/api-types";
import { queueHref } from "@/lib/claims";
import { formatCount, formatDate, formatPercent } from "@/lib/format";
import { programMetrics, remedyLabel } from "@/lib/programs";

import { StatusBadge } from "./StatusBadge";

/** Up to this many programs read better as cards than as a table row each. */
export const CARD_LIMIT = 4;

export type Portfolio = {
  open: number;
  total: number;
  units: number | null;
  filed: number;
  awaitingReview: number;
  corrected: number;
  correctionRate: number | null;
};

/** Totals across the firm's programs, from GET /v1/programs fields only. */
export function portfolio(programs: readonly Program[]): Portfolio {
  let units = 0;
  let unitsKnown = false;
  let filed = 0;
  let awaitingReview = 0;
  let corrected = 0;
  for (const p of programs) {
    const m = programMetrics(p);
    if (p.units_affected !== null && p.units_affected > 0) {
      units += p.units_affected;
      unitsKnown = true;
    }
    filed += m.filed;
    awaitingReview += m.awaitingReview;
    corrected += m.corrected;
  }
  return {
    open: programs.filter((p) => p.status === "open").length,
    total: programs.length,
    units: unitsKnown ? units : null,
    filed,
    awaitingReview,
    corrected,
    correctionRate: unitsKnown && units > 0 ? corrected / units : null,
  };
}

/** A tile like the program page's KPIs: the value at the foot, and an attention accent that adds no height. */
function Stat({ label, value, note, tone, className = "" }: { label: string; value: string; note?: string; tone?: "attention"; className?: string }) {
  return (
    <div
      className={`flex min-w-0 flex-col rounded-lg border bg-surface px-5 py-4 ${tone === "attention" ? "border-warn/40 border-t-warn shadow-[inset_0_1px_0_0_var(--warn)]" : "border-line"} ${className}`}
      data-portfolio-stat={label}
    >
      <dt className="eyebrow">{label}</dt>
      <dd className="kpi-value mt-auto pt-2">
        {value}
        {note ? <span className="ml-1.5 align-middle text-sm font-normal text-ink-muted">{note}</span> : null}
      </dd>
    </div>
  );
}

export function PortfolioSummary({ programs }: { programs: readonly Program[] }) {
  const t = portfolio(programs);
  return (
    // Five tiles in two columns would leave the last one alone: below xl the first spans the row and the other four
    // pair up under it. Five across waits for xl, where a fifth of the column still fits a six-digit value.
    <dl className="grid grid-cols-2 gap-3 xl:grid-cols-5" aria-label="All programs">
      <Stat label="Open programs" value={formatCount(t.open)} note={`of ${formatCount(t.total)}`} className="col-span-2 xl:col-span-1" />
      <Stat label="Units affected" value={formatCount(t.units)} />
      <Stat label="Claims filed" value={formatCount(t.filed)} />
      <Stat label="Awaiting review" value={formatCount(t.awaitingReview)} tone={t.awaitingReview > 0 ? "attention" : undefined} />
      <Stat label="Corrected" value={formatCount(t.corrected)} note={formatPercent(t.correctionRate)} />
    </dl>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: "attention" }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className={`mt-0.5 text-lg font-semibold tabular-nums ${tone === "attention" ? "text-warn-ink" : "text-ink"}`}>{value}</dd>
    </div>
  );
}

/** One card per program: what it is, where it stands, and where to go next. */
export function ProgramCards({ programs }: { programs: readonly Program[] }) {
  return (
    <ul className="grid gap-4" aria-label="Recall programs">
      {programs.map((p) => {
        const m = programMetrics(p);
        const models = p.identification_rules.models ?? [];
        return (
          <li key={p.id} className="rounded-xl border border-line bg-surface p-6" data-program-card={p.id}>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge kind="program" value={p.status} />
                  <span className="text-xs text-ink-muted">{p.opened_at ? `Opened ${formatDate(p.opened_at)}` : "Not opened"}</span>
                </div>
                <h2 className="mt-2 text-lg leading-snug font-semibold">
                  <Link href={`/app/programs/${p.id}`} className="text-ink">
                    {p.title}
                  </Link>
                </h2>
                <p className="mt-1 text-sm text-ink-muted">
                  {remedyLabel(p.remedy_rules.remedy_type)}
                  {models.length ? ` · ${models.length === 1 ? "Model" : "Models"} ${models.slice(0, 3).join(", ")}${models.length > 3 ? ` and ${models.length - 3} more` : ""}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-2 sm:shrink-0">
                <Link href={`/app/programs/${p.id}/reports`} className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-ink">
                  Monthly reports
                </Link>
                <Link href={queueHref(p.id, { status: null, cursor: null })} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90">
                  Open claims queue
                </Link>
              </div>
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-line pt-5 sm:grid-cols-5">
              <Figure label="Units affected" value={formatCount(p.units_affected)} />
              <Figure label="Claims filed" value={formatCount(m.filed)} />
              <Figure label="Awaiting review" value={formatCount(m.awaitingReview)} tone={m.awaitingReview > 0 ? "attention" : undefined} />
              <Figure label="Waiting on claimant" value={formatCount(m.needsInfo)} />
              <Figure label="Corrected" value={`${formatCount(m.corrected)} (${formatPercent(m.correctionRate)})`} />
            </dl>
          </li>
        );
      })}
    </ul>
  );
}
