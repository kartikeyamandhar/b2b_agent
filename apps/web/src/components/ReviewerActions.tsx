"use client";

import { useEffect, useRef, useState, type Ref } from "react";

import { ClaimDetail } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  ACTION_LABEL,
  NOTE_LIMIT,
  actionAvailability,
  actionProblemSentence,
  actionResultMessage,
  buildAction,
  isActionAvailable,
  isReviewQueued,
  type ActionKind,
} from "@/lib/claim-detail";

import { actionForKey, shortcutKey } from "@/lib/shortcuts";

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

/** The note or question box, capped at what the backend keeps (NOTE_LIMIT) with a visible count. */
export function ActionNote({ kind, message, onChange, ref }: { kind: ActionKind; message: string; onChange: (v: string) => void; ref?: Ref<HTMLTextAreaElement> }) {
  const limit = NOTE_LIMIT[kind];
  return (
    <>
      <label className="grid gap-1 text-xs">
        {kind === "request_info" ? "Question for the claimant (required)" : "Note for the audit trail (optional)"}
        <textarea
          ref={ref}
          value={message}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          maxLength={limit}
          required={kind === "request_info"}
          aria-describedby="claim-action-note-count"
          className="rounded border border-line bg-surface p-1 text-sm"
        />
      </label>
      <p id="claim-action-note-count" className={`text-xs ${message.length > limit ? "text-danger-ink" : "text-ink-muted"}`} data-note-count>
        {message.length} of {limit} characters{kind === "request_info" ? ". The claimant reads this on their claim page." : "."}
      </p>
    </>
  );
}

export type ActionDone = { updated: ClaimDetail; message: string; appliedByWorker: boolean; before: string };

/**
 * Reviewer actions with an inline confirmation step (no browser dialogs). Availability follows review.py.
 * `pending`: a review is queued for the worker, so every action and shortcut waits (the API would answer 409).
 * `onAlreadyQueued`: the API said a review is already queued (another tab or reviewer), so the page can wait for it.
 */
export function ReviewerActions({
  claim,
  onDone,
  pending = false,
  onAlreadyQueued,
}: {
  claim: ClaimDetail;
  onDone: (r: ActionDone) => void;
  pending?: boolean;
  onAlreadyQueued?: () => void;
}) {
  const availability = actionAvailability(claim);
  const [kind, setKind] = useState<ActionKind | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  // a, d, i open the matching confirmation (02-ui.md section 6); Escape closes it. Nothing is sent without Confirm.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Escape cancels even from the note box: it never types anything, so it cannot steal text.
      if (e.key === "Escape" && kind && !busy) {
        setKind(null);
        return;
      }
      const k = actionForKey(shortcutKey(e));
      if (!k || pending || availability.blocked || !isActionAvailable(k, availability) || busy) return;
      e.preventDefault();
      setKind(k);
      setError(null);
      setProblem(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [availability, kind, busy, pending]);

  useEffect(() => {
    if (kind) noteRef.current?.focus();
  }, [kind]);

  async function confirm() {
    if (!kind || pending) return;
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
      const p: ApiProblem = err instanceof ApiError ? err.problem : { status: 500, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
      if (isReviewQueued(p) && onAlreadyQueued) {
        // The page waits for that review instead; the pending note below says so.
        setKind(null);
        onAlreadyQueued();
      } else {
        setProblem(p);
      }
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
          {pending ? (
            <p role="status" className="mb-2 text-sm text-ink-muted" data-review-pending>
              A review is queued for this claim. Actions open again when the worker has applied it.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Choose an action">
            {KINDS.filter((k) => isActionAvailable(k, availability)).map((k) => (
              <button
                key={k}
                type="button"
                data-action={k}
                disabled={pending}
                aria-pressed={kind === k}
                onClick={() => {
                  setKind(k);
                  setError(null);
                  setProblem(null);
                }}
                className={`rounded-md border px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-brand ${
                  kind === k
                    ? "border-brand bg-brand-soft text-ink ring-1 ring-brand"
                    : k === "approve"
                      ? "border-accent bg-accent text-bg hover:opacity-90"
                      : k === "mark_fraud" || k === "deny"
                        ? "border-danger/40 bg-surface text-danger-ink hover:bg-danger-soft"
                        : "border-line-strong bg-surface text-ink hover:border-ink"
                } hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50`}
              >
                {ACTION_LABEL[k]}
              </button>
            ))}
          </div>
          <p className="mt-2 hidden text-xs text-ink-muted md:block" data-shortcut-hint>
            Keyboard: <kbd className="font-mono">a</kbd> approve · <kbd className="font-mono">d</kbd> deny · <kbd className="font-mono">i</kbd> request info · <kbd className="font-mono">Esc</kbd> cancel. Each still asks you to confirm.
          </p>
          {availability.appliedByWorker ? (
            <p className="mt-2 text-xs text-ink-muted">The run is paused for review: the worker resumes it with your decision.</p>
          ) : null}
          {kind && !pending ? (
            <form
              className="mt-3 grid gap-2 rounded border border-line bg-panel p-2"
              onSubmit={(e) => {
                e.preventDefault();
                void confirm();
              }}
            >
              <p className="text-sm">{CONFIRM_HINT[kind]}</p>
              <ActionNote ref={noteRef} kind={kind} message={message} onChange={setMessage} />
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
          <ProblemAlert problem={problem} sentence={actionProblemSentence(problem)} showDetail={actionProblemSentence(problem) === undefined} />
        </div>
      ) : null}
    </Panel>
  );
}
