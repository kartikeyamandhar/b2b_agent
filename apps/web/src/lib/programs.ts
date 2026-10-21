// Program figures derived from GET /v1/programs fields only (no endpoint beyond the contract).
import type { Program } from "@/lib/api-types";

/** Claim statuses in queue order: issued, in flight, waiting on a person, decided, delivered. */
export const CLAIM_STATUS_ORDER = [
  "draft",
  "submitted",
  "needs_info",
  "flagged",
  "under_review",
  "capped",
  "approved",
  "denied",
  "paid",
  "closed",
] as const;

/** Statuses where a reviewer must act (trace.md: key reviewer actions on the claim status). */
export const AWAITING_REVIEW = ["flagged", "under_review", "capped"] as const;

/** A correction is a refund paid or a repair or replacement delivered (CHANGES.md item 6). */
export const CORRECTED = ["paid", "closed"] as const;

export type ProgramMetrics = {
  /** Links issued that the claimant has not submitted yet (status draft). */
  linksOpen: number;
  /** Every claim past draft. */
  filed: number;
  awaitingReview: number;
  needsInfo: number;
  corrected: number;
  /** corrected / units affected; null when units are unknown or zero. */
  correctionRate: number | null;
};

const count = (counts: Record<string, number>, status: string): number => {
  const n = Object.hasOwn(counts, status) ? counts[status] : undefined;
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : 0;
};

export function programMetrics(p: Pick<Program, "claim_counts" | "units_affected">): ProgramMetrics {
  const counts = p.claim_counts;
  const total = Object.keys(counts).reduce((sum, s) => sum + count(counts, s), 0);
  const linksOpen = count(counts, "draft");
  const corrected = CORRECTED.reduce((sum, s) => sum + count(counts, s), 0);
  const units = p.units_affected;
  return {
    linksOpen,
    filed: total - linksOpen,
    awaitingReview: AWAITING_REVIEW.reduce((sum, s) => sum + count(counts, s), 0),
    needsInfo: count(counts, "needs_info"),
    corrected,
    correctionRate: units && units > 0 ? corrected / units : null,
  };
}

export type StatusRow = { status: string; count: number; shareOfFiled: number | null };

/** Every known status in queue order (zeros included), then any status the backend added later. */
export function statusRows(counts: Record<string, number>): StatusRow[] {
  const known = new Set<string>(CLAIM_STATUS_ORDER);
  const extra = Object.keys(counts).filter((s) => !known.has(s)).sort();
  const filed = Object.keys(counts).reduce((sum, s) => (s === "draft" ? sum : sum + count(counts, s)), 0);
  return [...CLAIM_STATUS_ORDER, ...extra].map((status) => {
    const n = count(counts, status);
    return { status, count: n, shareOfFiled: status === "draft" || filed === 0 ? null : n / filed };
  });
}

const REMEDY_LABEL: Record<string, string> = {
  refund: "Refund",
  repair: "Repair",
  replacement: "Replacement",
  multiple: "More than one remedy",
};

export function remedyLabel(value: string | null | undefined): string {
  if (!value) return "Not stated in the notice";
  return Object.hasOwn(REMEDY_LABEL, value) ? REMEDY_LABEL[value]! : value;
}
