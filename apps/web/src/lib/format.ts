// Display formatting. Fixed locale and UTC so server output is stable and matches what tests assert.

const COUNT = new Intl.NumberFormat("en-US");
const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const DATE = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" });
const PERCENT = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 });

export const NOT_STATED = "Not stated";

export function formatCount(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : COUNT.format(n);
}

/** Money is integer cents in the API (docs/onboarding/ui.md). */
export function formatCents(cents: number | null | undefined): string {
  return cents === null || cents === undefined ? "—" : USD.format(cents / 100);
}

/** Accepts a timestamp or a YYYY-MM-DD date; a date is read as that UTC day, not shifted by time zone. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
  return Number.isNaN(d.getTime()) ? value : DATE.format(d);
}

export function formatPercent(ratio: number | null): string {
  return ratio === null ? "—" : PERCENT.format(ratio);
}

const PERCENT_FIXED = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** One decimal always, so a column of percentages lines up ("22.0%" beside "21.4%"). */
export function formatPercentFixed(ratio: number | null): string {
  return ratio === null ? "—" : PERCENT_FIXED.format(ratio);
}

const USD_COST = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** Model spend, a decimal string in dollars from the API ("0.000000", "0.037476"): "$0.00", "$0.0375". */
export function formatUsdCost(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? USD_COST.format(n) : String(value);
}

const DATE_TIME = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "UTC",
});
const TIME = new Intl.DateTimeFormat("en-US", { timeStyle: "medium", hourCycle: "h23", timeZone: "UTC" });

/** Queue ages matter, so timestamps keep the time; always UTC, labelled. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : `${DATE_TIME.format(d)} UTC`;
}

export function formatClock(ms: number): string {
  return `${TIME.format(new Date(ms))} UTC`;
}

/** "1 payout", "2 payouts": a count with its noun, singular for exactly one. */
export function countOf(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${formatCount(n)} ${n === 1 ? singular : pluralForm}`;
}
