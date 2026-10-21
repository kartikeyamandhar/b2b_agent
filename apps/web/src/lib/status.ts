// Console labels for the backend's status vocabularies (docs/contracts/trace.md, CHANGES.md items 5 and 6).
// A value the backend adds later renders as "In progress" instead of failing (CHANGES.md item 5).

export type Tone = "neutral" | "progress" | "done" | "attention" | "negative";
export type StatusKind = "claim" | "run" | "payout" | "outcome";
export type StatusLabel = { label: string; tone: Tone; known: boolean };

const CLAIM: Record<string, Omit<StatusLabel, "known">> = {
  draft: { label: "Link issued", tone: "neutral" },
  submitted: { label: "Submitted", tone: "progress" },
  needs_info: { label: "Needs info", tone: "attention" },
  under_review: { label: "Under review", tone: "attention" },
  approved: { label: "Approved", tone: "done" },
  denied: { label: "Denied", tone: "negative" },
  flagged: { label: "Flagged", tone: "attention" },
  capped: { label: "Capped", tone: "attention" },
  paid: { label: "Paid", tone: "done" },
  closed: { label: "Closed", tone: "done" },
};

const RUN: Record<string, Omit<StatusLabel, "known">> = {
  running: { label: "Running", tone: "progress" },
  done: { label: "Done", tone: "done" },
  capped: { label: "Capped", tone: "attention" },
  failed: { label: "Failed", tone: "negative" },
  waiting_human: { label: "Waiting for reviewer", tone: "attention" },
};

const PAYOUT: Record<string, Omit<StatusLabel, "known">> = {
  pending: { label: "Pending", tone: "progress" },
  pending_export: { label: "Waiting for check export", tone: "progress" },
  onboarding: { label: "Claimant onboarding", tone: "progress" },
  transferring: { label: "Transfer in flight", tone: "progress" },
  transferred: { label: "Transferred", tone: "done" },
  failed: { label: "Transfer failed", tone: "negative" },
  exported: { label: "Check exported", tone: "done" },
  held: { label: "On hold, a person is reviewing", tone: "attention" },
};

const OUTCOME: Record<string, Omit<StatusLabel, "known">> = {
  approve: { label: "Approve", tone: "done" },
  deny: { label: "Deny", tone: "negative" },
  needs_info: { label: "Needs info", tone: "attention" },
  flag: { label: "Flag", tone: "attention" },
};

const TABLES: Record<StatusKind, Record<string, Omit<StatusLabel, "known">>> = {
  claim: CLAIM,
  run: RUN,
  payout: PAYOUT,
  outcome: OUTCOME,
};

export function statusLabel(kind: StatusKind, value: string | null | undefined): StatusLabel {
  if (value === null || value === undefined || value === "") return { label: "None", tone: "neutral", known: true };
  const hit = Object.hasOwn(TABLES[kind], value) ? TABLES[kind][value] : undefined;
  return hit ? { ...hit, known: true } : { label: "In progress", tone: "progress", known: false };
}

export function knownValues(kind: StatusKind): string[] {
  return Object.keys(TABLES[kind]);
}
