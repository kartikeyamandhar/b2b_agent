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
