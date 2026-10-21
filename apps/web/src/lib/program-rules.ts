// Program settings (PATCH /v1/programs/{id}): the rule editor's form model, its conversion back to the contract's
// rule shapes, and which lifecycle moves the backend allows (programs.py patch_program). Pure functions, so the
// rules are unit-tested; the API still validates every field (rules/schemas.py, extra="forbid").
import { ProgramPatch, type IdentificationRules, type Program, type RemedyRules } from "@/lib/api-types";

export type SerialRow = { raw: string; prefix: string; start: string; end: string };
export type DateCodeRow = { raw: string; format: string; start: string; end: string };

/** Every editable field as text, the way the form holds it. */
export type RulesDraft = {
  models: string;
  modelLabelLocation: string;
  soldAt: string;
  soldFrom: string;
  soldTo: string;
  priceMin: string;
  priceMax: string;
  serialRanges: SerialRow[];
  dateCodes: DateCodeRow[];
  remedyType: string;
  destructionInstruction: string;
  registrationUrl: string;
  contact: string;
  threshold: string;
};

export const REMEDY_TYPES = ["refund", "repair", "replacement", "multiple"] as const;

/** Formats the range check reads (claims/tools/checks.py); others are kept and compared as text. */
export const DATE_CODE_FORMATS = ["MM/YY", "MM/YYYY", "MM-YY", "MMYY", "YYWW", "Month YYYY", "model year", "date code"] as const;

const lines = (v: string): string[] => {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of v.split(/\r?\n/)) {
    const s = raw.trim();
    if (s && !seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
};

const dollars = (cents: number | null | undefined): string => (cents === null || cents === undefined ? "" : (cents / 100).toFixed(2));

export function draftFromProgram(p: Pick<Program, "identification_rules" | "remedy_rules" | "value_threshold_cents">): RulesDraft {
  const i = p.identification_rules;
  const r = p.remedy_rules;
  return {
    models: (i.models ?? []).join("\n"),
    modelLabelLocation: i.model_label_location ?? "",
    soldAt: (i.sold_at ?? []).join("\n"),
    soldFrom: i.sold_from ?? "",
    soldTo: i.sold_to ?? "",
    priceMin: dollars(i.price_range_cents?.min_cents),
    priceMax: dollars(i.price_range_cents?.max_cents),
    serialRanges: (i.serial_ranges ?? []).map((s) => ({ raw: s.raw, prefix: s.prefix ?? "", start: s.start ?? "", end: s.end ?? "" })),
    dateCodes: (i.date_codes ?? []).map((d) => ({ raw: d.raw, format: d.format, start: d.start ?? "", end: d.end ?? "" })),
    remedyType: r.remedy_type ?? "",
    destructionInstruction: r.destruction_instruction ?? "",
    registrationUrl: r.registration_url ?? "",
    contact: r.contact ?? "",
    threshold: dollars(p.value_threshold_cents),
  };
}

/** Dollars typed by a person ("1,250.5", "$40") to integer cents; null when it is not a non-negative amount. */
export function parseDollars(value: string): number | null {
  const s = value.trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const cents = Math.round(Number(s) * 100);
  return Number.isSafeInteger(cents) ? cents : null;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
function isDate(v: string): boolean {
  if (!ISO_DATE.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
}

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

export type FieldErrors = Partial<Record<keyof RulesDraft, string>>;
export type RulesResult = { ok: true; patch: ProgramPatch } | { ok: false; errors: FieldErrors };

/**
 * The draft as a PATCH body. Fields the editor does not show (evidence spans, remedy options, units affected, and
 * any field a newer contract adds) are carried over from the program unchanged, because PATCH replaces each rules
 * object whole.
 */
export function rulesPatch(draft: RulesDraft, original: Pick<Program, "identification_rules" | "remedy_rules">): RulesResult {
  const errors: FieldErrors = {};
  const soldFrom = draft.soldFrom.trim();
  const soldTo = draft.soldTo.trim();
  if (soldFrom && !isDate(soldFrom)) errors.soldFrom = "Use a date like 2026-01-31.";
  if (soldTo && !isDate(soldTo)) errors.soldTo = "Use a date like 2026-01-31.";
  if (!errors.soldFrom && !errors.soldTo && soldFrom && soldTo && soldFrom > soldTo) errors.soldTo = "The end of the sale window is before its start.";

  let price: { min_cents: number; max_cents: number } | null = null;
  const pMin = draft.priceMin.trim();
  const pMax = draft.priceMax.trim();
  if (pMin || pMax) {
    const lo = parseDollars(pMin);
    const hi = parseDollars(pMax);
    if (lo === null) errors.priceMin = "Enter both prices as dollar amounts, or leave both empty.";
    if (hi === null) errors.priceMax = "Enter both prices as dollar amounts, or leave both empty.";
    if (lo !== null && hi !== null) {
      if (lo > hi) errors.priceMax = "The highest price is below the lowest.";
      else price = { min_cents: lo, max_cents: hi };
    }
  }

  const serials = draft.serialRanges.filter((s) => s.raw.trim() || s.prefix.trim() || s.start.trim() || s.end.trim());
  if (serials.some((s) => !s.raw.trim())) errors.serialRanges = "Each serial range needs the text as the notice states it.";
  const codes = draft.dateCodes.filter((d) => d.raw.trim() || d.format.trim() || d.start.trim() || d.end.trim());
  if (codes.some((d) => !d.raw.trim() || !d.format.trim())) errors.dateCodes = "Each date code needs its text and its format.";

  const threshold = parseDollars(draft.threshold);
  if (threshold === null) errors.threshold = "Enter a dollar amount, for example 100.00.";

  if (draft.remedyType && !(REMEDY_TYPES as readonly string[]).includes(draft.remedyType)) errors.remedyType = "Choose a remedy.";

  const url = draft.registrationUrl.trim();
  if (url && /^(javascript|data|vbscript):/i.test(url)) errors.registrationUrl = "Enter a web address.";

  if (Object.keys(errors).length) return { ok: false, errors };

  const identification: IdentificationRules = {
    ...original.identification_rules,
    models: lines(draft.models),
    model_label_location: orNull(draft.modelLabelLocation),
    sold_at: lines(draft.soldAt),
    sold_from: soldFrom || null,
    sold_to: soldTo || null,
    price_range_cents: price,
    serial_ranges: serials.map((s) => ({ raw: s.raw.trim(), prefix: orNull(s.prefix), start: orNull(s.start), end: orNull(s.end) })),
    date_codes: codes.map((d) => ({ raw: d.raw.trim(), format: d.format.trim(), start: orNull(d.start), end: orNull(d.end) })),
  };
  const remedy: RemedyRules = {
    ...original.remedy_rules,
    remedy_type: (draft.remedyType || null) as RemedyRules["remedy_type"],
    destruction_instruction: orNull(draft.destructionInstruction),
    registration_url: orNull(url),
    contact: orNull(draft.contact),
  };
  const patch = ProgramPatch.safeParse({ identification_rules: identification, remedy_rules: remedy, value_threshold_cents: threshold });
  if (!patch.success) return { ok: false, errors: { threshold: "Some fields could not be read. Check the values and try again." } };
  return { ok: true, patch: patch.data };
}

/** True when the form differs from what is saved (the Save button and the leave warning use it). */
export function draftChanged(a: RulesDraft, b: RulesDraft): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

export type Lifecycle = {
  /** The move this program can make next, or null when none is left. */
  next: "open" | "closed" | null;
  /** Rules and threshold can be edited (not on a closed program). */
  editable: boolean;
};

/** draft → open → closed, never back (programs.py: "a closed program cannot be reopened"). */
export function lifecycle(status: string): Lifecycle {
  if (status === "draft") return { next: "open", editable: true };
  if (status === "open") return { next: "closed", editable: true };
  return { next: null, editable: false };
}

/** What a reader should know about a program with no identifiers: the adjudicator can confirm nothing. */
export function rulesWarnings(p: Pick<Program, "identification_rules" | "remedy_rules">): string[] {
  const i = p.identification_rules;
  const out: string[] = [];
  if ((i.models ?? []).length === 0 && (i.serial_ranges ?? []).length === 0 && (i.date_codes ?? []).length === 0) {
    out.push("No model numbers, serial ranges, or date codes were found. Claims cannot be matched to the recall until you add at least one.");
  }
  if (!p.remedy_rules.remedy_type) out.push("The remedy is not set.");
  return out;
}

/** Which evidence fields back each editor field (rules/verify.py stores bare or path-qualified names). */
export const EVIDENCE_FIELDS: Partial<Record<keyof RulesDraft, readonly string[]>> = {
  models: ["models"],
  modelLabelLocation: ["model_label_location"],
  soldAt: ["sold_at"],
  soldFrom: ["sold_from", "sold_to"],
  priceMin: ["price_range_cents"],
  serialRanges: ["serial_ranges"],
  dateCodes: ["date_codes"],
  remedyType: ["remedy_type"],
  destructionInstruction: ["destruction_instruction"],
  registrationUrl: ["registration_url"],
  contact: ["contact"],
};

/** Notice quotes per editor field, de-duplicated, in notice order. A quote means the value was found verbatim. */
export function evidenceByField(p: Pick<Program, "identification_rules" | "remedy_rules">): Partial<Record<keyof RulesDraft, string[]>> {
  const spans = [...(p.identification_rules.evidence ?? []), ...(p.remedy_rules.evidence ?? [])].slice().sort((a, b) => a.start - b.start);
  const out: Partial<Record<keyof RulesDraft, string[]>> = {};
  for (const [key, fields] of Object.entries(EVIDENCE_FIELDS) as [keyof RulesDraft, readonly string[]][]) {
    const quotes = spans.filter((s) => fields.includes(s.field.split(".").pop() ?? s.field)).map((s) => s.quote.trim()).filter(Boolean);
    if (quotes.length) out[key] = [...new Set(quotes)];
  }
  return out;
}
