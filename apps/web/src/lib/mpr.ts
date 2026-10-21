// Monthly progress report (CPSC MPR) display rules. The payload is typed only as an object in the contract, so it
// is read leniently here: unknown boxes still render, in the order the backend sends (the CPSC form order).
import { z } from "zod";

export const MprPayload = z.looseObject({
  period: z.string().optional(),
  boxes: z.record(z.string(), z.unknown()).default({}),
  computed: z.record(z.string(), z.unknown()).default({}),
  manual: z.record(z.string(), z.unknown()).default({}),
  notes: z.array(z.string()).default([]),
  reconciliation: z.looseObject({ discrepancies: z.array(z.unknown()).default([]) }).optional(),
});
export type MprPayload = z.infer<typeof MprPayload>;

/** CPSC form order (apps/api mpr/generate.py BOX_ORDER). Postgres JSONB does not keep key order, so the UI sorts. */
export const BOX_ORDER = [
  "total_units_affected", "corrections_this_period", "cumulative_corrections", "units_with_manufacturer",
  "units_with_distributor", "units_with_retailers", "units_with_consumers", "incidents_before", "incidents_after",
  "injuries_before", "injuries_after", "deaths_before", "deaths_after", "notified_phone_text", "notified_email",
  "notified_mail", "contacts_phone", "contacts_email", "contacts_mail", "contacts_registration_card", "homepage_posting",
  "homepage_posting_explanation", "website_hits", "social_posts", "marketplace_monitoring",
] as const;

/** Boxes in form order; boxes the backend adds later follow, alphabetically. */
export function orderedBoxes(boxes: Record<string, unknown>): Array<[string, unknown]> {
  const known: readonly string[] = BOX_ORDER;
  const rank = (k: string) => (known.includes(k) ? known.indexOf(k) : known.length);
  return Object.entries(boxes).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

const LABELS: Record<string, string> = {
  total_units_affected: "Total units affected",
  corrections_this_period: "Corrections this period",
  cumulative_corrections: "Cumulative corrections",
  units_with_manufacturer: "Units with the manufacturer",
  units_with_distributor: "Units with distributors",
  units_with_retailers: "Units with retailers",
  units_with_consumers: "Units with consumers",
  incidents_before: "Incidents before the recall",
  incidents_after: "Incidents after the recall",
  injuries_before: "Injuries before the recall",
  injuries_after: "Injuries after the recall",
  deaths_before: "Deaths before the recall",
  deaths_after: "Deaths after the recall",
  notified_phone_text: "Consumers notified by phone or text",
  notified_email: "Consumers notified by email",
  notified_mail: "Consumers notified by mail",
  contacts_phone: "Consumers who contacted by phone",
  contacts_email: "Consumers who contacted by email or the portal",
  contacts_mail: "Consumers who contacted by mail",
  contacts_registration_card: "Registration cards received",
  homepage_posting: "Recall posted on the homepage",
  homepage_posting_explanation: "Homepage posting explanation",
  website_hits: "Website hits",
  social_posts: "Social media posts and reach",
  marketplace_monitoring: "Marketplace monitoring",
};

export function boxLabel(key: string): string {
  return Object.hasOwn(LABELS, key) ? LABELS[key]! : key.replace(/_/g, " ");
}

export function boxValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return new Intl.NumberFormat("en-US").format(v);
  if (typeof v === "string") return v;
  if (typeof v === "object" && !Array.isArray(v)) {
    const e = Object.entries(v as Record<string, unknown>);
    return e.length ? e.map(([k, x]) => `${k.replace(/_/g, " ")} ${boxValue(x)}`).join(" · ") : "—";
  }
  return JSON.stringify(v);
}

export type BoxSource = "computed" | "manual" | "default";

export function boxSource(key: string, p: Pick<MprPayload, "computed" | "manual">): BoxSource {
  if (Object.hasOwn(p.manual, key)) return "manual";
  if (Object.hasOwn(p.computed, key)) return "computed";
  return "default";
}

const PERIOD = /^\d{4}-(0[1-9]|1[0-2])$/;

export function isPeriod(v: string | undefined): v is string {
  return !!v && PERIOD.test(v);
}

/** Months from the program's start to now, newest first (UTC). */
export function reportPeriods(startIso: string, now: Date, max = 24): string[] {
  const start = new Date(startIso);
  const out: string[] = [];
  let y = now.getUTCFullYear();
  let m = now.getUTCMonth();
  const sy = Number.isNaN(start.getTime()) ? y : start.getUTCFullYear();
  const sm = Number.isNaN(start.getTime()) ? m : start.getUTCMonth();
  while ((y > sy || (y === sy && m >= sm)) && out.length < max) {
    out.push(`${y}-${String(m + 1).padStart(2, "0")}`);
    m -= 1;
    if (m < 0) {
      m = 11;
      y -= 1;
    }
  }
  return out.length ? out : [`${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`];
}

export function periodLabel(period: string): string {
  const [y, m] = period.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(y!, (m ?? 1) - 1, 1)));
}

/** MprManual fields the form edits (integer boxes plus the homepage posting); object boxes stay at their defaults. */
export const MANUAL_NUMBER_FIELDS = [
  "units_with_manufacturer",
  "units_with_distributor",
  "units_with_retailers",
  "incidents_before",
  "incidents_after",
  "injuries_before",
  "injuries_after",
  "deaths_before",
  "deaths_after",
  "notified_phone_text",
  "notified_email",
  "notified_mail",
  "contacts_phone",
  "contacts_email",
  "contacts_mail",
  "contacts_registration_card",
  "website_hits",
] as const;
