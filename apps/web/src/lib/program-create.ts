// Creating a program (POST /v1/programs, admin) from a recall found with GET /v1/recalls, or from a notice typed in
// by hand. The backend extracts rules (regex first; the model only as a fallback) and returns a draft program.
import { ProgramCreate } from "@/lib/api-types";

import { parseDollars } from "./program-rules";

export const RECALL_PAGE_LIMIT = 25;

/** Query string for the recall search; an empty query lists the newest recalls. */
export function recallSearch(q: string, since: string, limit = RECALL_PAGE_LIMIT): string {
  const params = new URLSearchParams();
  const query = q.trim().slice(0, 200);
  if (query) params.set("q", query);
  if (/^\d{4}-\d{2}-\d{2}$/.test(since.trim())) params.set("since", since.trim());
  params.set("limit", String(Math.min(Math.max(1, Math.trunc(limit)), 100)));
  return `?${params.toString()}`;
}

/** A recall's public notice link, only when it is an https address on a CPSC host; anything else is not linked. */
export function noticeHref(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return null;
    const host = u.hostname.toLowerCase();
    return host === "cpsc.gov" || host.endsWith(".cpsc.gov") || host === "saferproducts.gov" || host.endsWith(".saferproducts.gov") ? u.toString() : null;
  } catch {
    return null;
  }
}

export type CreateInput =
  | { source: "recall"; recallId: string; threshold: string }
  | { source: "manual"; title: string; description: string; recallDate: string; threshold: string };

export type CreateErrors = Partial<Record<"recall" | "title" | "description" | "recallDate" | "threshold", string>>;

export const MANUAL_DESCRIPTION_MIN = 40;

export function createBody(input: CreateInput): { ok: true; body: ProgramCreate } | { ok: false; errors: CreateErrors } {
  const errors: CreateErrors = {};
  let threshold: number | null = null;
  if (input.threshold.trim()) {
    threshold = parseDollars(input.threshold);
    if (threshold === null) errors.threshold = "Enter a dollar amount, or leave it empty for the default.";
  }
  let body: unknown;
  if (input.source === "recall") {
    if (!input.recallId) errors.recall = "Choose a recall from the search results.";
    body = { recall_id: input.recallId, value_threshold_cents: threshold };
  } else {
    const title = input.title.trim();
    const description = input.description.trim();
    const date = input.recallDate.trim();
    if (!title) errors.title = "Enter the recall title.";
    else if (title.length > 300) errors.title = "Keep the title under 300 characters.";
    if (description.length < MANUAL_DESCRIPTION_MIN) errors.description = `Paste the notice text (at least ${MANUAL_DESCRIPTION_MIN} characters), including model numbers and the remedy.`;
    if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) errors.recallDate = "Use a date like 2026-10-01.";
    body = { manual: { title, description, recall_date: date || null }, value_threshold_cents: threshold };
  }
  if (Object.keys(errors).length) return { ok: false, errors };
  const parsed = ProgramCreate.safeParse(body);
  if (!parsed.success) return { ok: false, errors: { recall: "The program could not be prepared. Check the fields and try again." } };
  return { ok: true, body: parsed.data };
}

/** Recall ids that already have a program in this firm, so the search can say so. */
export function programRecallIds(programs: readonly { recall_id: string | null }[]): Set<string> {
  return new Set(programs.map((p) => p.recall_id).filter((v): v is string => typeof v === "string"));
}
