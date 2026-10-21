"use client";

import { useState } from "react";

import { ClaimDetail } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  ACTION_LABEL,
  actionAvailability,
  actionResultMessage,
  buildAction,
  isActionAvailable,
  type ActionKind,
} from "@/lib/claim-detail";

import { Panel } from "./ClaimPanels";
import { ProblemAlert } from "./ProblemAlert";

const KINDS: readonly ActionKind[] = ["approve", "deny", "request_info", "mark_fraud", "mark_delivered"];

const CONFIRM_HINT: Record<ActionKind, string> = {
  approve: "Approve the claim. An approved refund creates a payout.",
  deny: "Deny the claim. The claimant sees the reasons in plain language.",
  request_info: "Ask the claimant a question. They answer on their claim page.",
  mark_fraud: "Deny the claim as fraud. This is recorded in the audit trail.",
  mark_delivered: "Record that the repair or replacement reached the claimant. The claim closes.",
};

export type ActionDone = { updated: ClaimDetail; message: string; appliedByWorker: boolean; before: string };

/** Reviewer actions with an inline confirmation step (no browser dialogs). Availability follows review.py. */
export function ReviewerActions({ claim, onDone }: { claim: ClaimDetail; onDone: (r: ActionDone) => void }) {
  const availability = actionAvailability(claim);
  const [kind, setKind] = useState<ActionKind | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);

  async function confirm() {
    if (!kind) return;
    const built = buildAction(kind, message);
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setBusy(true);
    setError(null);
    setProblem(null);
    try {
      const updated = await sendJson(ClaimDetail, "POST", `/v1/claims/${claim.id}/actions`, built.body);
      onDone({
        updated,
        before: claim.status,
        appliedByWorker: availability.appliedByWorker,
        message: actionResultMessage(kind, claim.status, updated.status, availability.appliedByWorker),
      });
      setKind(null);
      setMessage("");
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 500, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel id="claim-actions" title="Reviewer actions">
      {availability.blocked ? (
        <p className="text-sm text-ink-muted" data-actions-blocked>
          {availability.blocked}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Choose an action">
            {KINDS.filter((k) => isActionAvailable(k, availability)).map((k) => (
              <button
                key={k}
                type="button"
                data-action={k}
                aria-pressed={kind === k}
                onClick={() => {
                  setKind(k);
                  setError(null);
                  setProblem(null);
                }}
                className={`rounded-md border px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-copper ${
                  kind === k
                    ? "border-copper bg-copper-soft text-ink ring-1 ring-copper"
                    : k === "approve"
                      ? "border-accent bg-accent text-bg hover:opacity-90"
                      : k === "mark_fraud" || k === "deny"
                        ? "border-danger/40 bg-surface text-danger-ink hover:bg-danger-soft"
                        : "border-line-strong bg-surface text-ink hover:border-ink"
                } hover:opacity-90`}
              >
                {ACTION_LABEL[k]}
              </button>
            ))}
          </div>
          {availability.appliedByWorker ? (
            <p className="mt-2 text-xs text-ink-muted">The run is paused for review: the worker resumes it with your decision.</p>
          ) : null}
          {kind ? (
            <form
              className="mt-3 grid gap-2 rounded border border-line bg-panel p-2"
              onSubmit={(e) => {
                e.preventDefault();
                void confirm();
              }}
            >
              <p className="text-sm">{CONFIRM_HINT[kind]}</p>
              <label className="grid gap-1 text-xs">
                {kind === "request_info" ? "Question for the claimant (required)" : "Note for the audit trail (optional)"}
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={2}
                  maxLength={1000}
                  required={kind === "request_info"}
                  className="rounded border border-line bg-surface p-1 text-sm"
                />
              </label>
              {error ? (
                <p role="alert" className="text-xs text-warn-ink">
                  {error}
                </p>
              ) : null}
              <div className="flex gap-2">
                <button type="submit" disabled={busy} className="rounded-md border border-accent bg-accent px-2 py-1 text-xs font-medium text-bg disabled:opacity-60 hover:opacity-90">
                  {busy ? "Sending…" : `Confirm ${ACTION_LABEL[kind].toLowerCase()}`}
                </button>
                <button type="button" onClick={() => setKind(null)} className="rounded-lg border border-line bg-surface px-2 py-1 text-xs">
                  Cancel
                </button>
              </div>
            </form>
          ) : null}
        </>
      )}
      {problem ? (
        <div className="mt-2">
          <ProblemAlert problem={problem} />
        </div>
      ) : null}
    </Panel>
  );
}
