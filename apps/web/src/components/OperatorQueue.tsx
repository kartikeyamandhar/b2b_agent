"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { OperatorAccessRequest } from "@/lib/api-types";
import { fetchJson, sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  OPERATOR_ACTION_LABEL,
  OPERATOR_POLL_MS,
  OperatorAccessList,
  REVOKE_CONFIRM,
  accessBadgeClass,
  accessStatusLabel,
  decisionOutcome,
  domainHint,
  emailWarnings,
  newestFirst,
  operatorActions,
  recallLabel,
  requesterLabel,
} from "@/lib/access";
import { planLabel } from "@/lib/billing";
import { focusOnOpen } from "@/lib/focus";
import { formatCount, formatDateTime } from "@/lib/format";

import { ProblemAlert } from "./ProblemAlert";

type Decision = "approved" | "rejected" | "revoked";
export type DecisionNotice = { id: string; text: string };

export const OPERATOR_QUERY_KEY = ["operator-access"] as const;

/** The list for the operator window, read again every 10 seconds while the window is open. */
export function operatorQuery() {
  return {
    queryKey: OPERATOR_QUERY_KEY,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchJson(OperatorAccessList, "/v1/operator/access", "", signal),
    refetchInterval: OPERATOR_POLL_MS,
    refetchIntervalInBackground: false,
    retry: false,
    // The server's first read is fresh: no second read right after the page loads.
    staleTime: OPERATOR_POLL_MS / 2,
  };
}

/** The operator window's live list: the server's first read, then polling. A failed poll keeps the last list. */
export function OperatorQueueLive({ initialRequests, loadedAt }: { initialRequests: OperatorAccessRequest[]; loadedAt: number }) {
  const client = useQueryClient();
  const q = useQuery({ ...operatorQuery(), initialData: initialRequests, initialDataUpdatedAt: loadedAt });
  return (
    <OperatorQueue
      requests={q.data}
      onDecided={() => void client.invalidateQueries({ queryKey: OPERATOR_QUERY_KEY })}
      stale={q.isError ? (q.error instanceof ApiError ? q.error.problem : null) : null}
    />
  );
}

/** Companies' requests to run a recall: what is waiting, newest first, and a folded list of what was decided. Each
 *  decision's outcome stays at the top, so the operator reads what happened to the company's product. */
export function OperatorQueue({ requests, onDecided, stale = null }: { requests: readonly OperatorAccessRequest[]; onDecided?: () => void; stale?: ApiProblem | null }) {
  const router = useRouter();
  const [notices, setNotices] = useState<DecisionNotice[]>([]);
  const pending = newestFirst(requests.filter((r) => r.status === "requested"));
  const decided = newestFirst(requests.filter((r) => r.status !== "requested"));

  function decidedOne(r: OperatorAccessRequest, text: string) {
    setNotices((prev) => [{ id: `${r.id}-${prev.length}`, text: `${r.firm_name}, ${recallLabel(r)}. ${text}` }, ...prev]);
    if (onDecided) onDecided();
    else router.refresh();
  }

  return (
    <div className="grid w-full max-w-4xl gap-6" data-operator-queue>
      <header className="grid gap-2">
        <h1 className="page-title">Requests to run a recall</h1>
        <p className="text-ink-muted">
          Approve a company only when you are sure it issued the recall: a work email on the company&rsquo;s domain, or a call to the contact number
          in the notice. One company at most runs each recall. Approving opens the company&rsquo;s product at once; revoking pauses it and stops
          its claim link.
        </p>
      </header>
      {notices.length ? (
        <ul role="status" className="grid gap-2" data-decision-notices>
          {notices.map((n) => (
            <li key={n.id} className="rounded-lg border border-brand/40 border-l-4 border-l-brand bg-brand-soft px-4 py-3 text-sm text-ink">
              {n.text}
            </li>
          ))}
        </ul>
      ) : null}
      {stale ? <ProblemAlert problem={stale} sentence="The list could not be refreshed. It shows the last answer and tries again shortly." /> : null}
      <section aria-labelledby="pending" className="grid gap-3">
        <h2 id="pending" className="section-title">
          Waiting for review ({pending.length})
        </h2>
        {pending.length ? pending.map((r) => <RequestCard key={r.id} r={r} onDecided={decidedOne} />) : <p className="text-sm text-ink-muted">Nothing is waiting.</p>}
      </section>
      {decided.length ? (
        <details data-decided>
          <summary className="section-title cursor-pointer">Decided ({decided.length})</summary>
          <div className="mt-3 grid gap-3">
            {decided.map((r) => (
              <RequestCard key={r.id} r={r} onDecided={decidedOne} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

/** Why Approve is off: the API refuses to approve a recall another company already runs (409). */
export const APPROVED_ELSEWHERE_REASON = "Another company runs this recall. Reject this request, or revoke that company's approval first.";

/** The inline confirmation before a revoke; it takes focus when it opens, and nothing is sent until Revoke access. */
export function RevokeConfirm({ busy, onConfirm, onCancel }: { busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div role="group" aria-label="Confirm revoking" className="grid gap-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink" data-revoke-confirm>
      <p ref={focusOnOpen} tabIndex={-1} className="font-medium outline-none">
        {REVOKE_CONFIRM}
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={onConfirm} className="rounded-md border border-danger/40 bg-danger-soft px-3 py-1.5 font-semibold text-danger-ink disabled:opacity-60" data-confirm-revoke>
          {busy ? "Revoking…" : "Revoke access"}
        </button>
        <button type="button" disabled={busy} onClick={onCancel} className="rounded-md border border-line-strong bg-surface px-3 py-1.5 font-medium text-ink hover:border-ink">
          Keep access
        </button>
      </div>
    </div>
  );
}

export function RequestCard({ r, onDecided }: { r: OperatorAccessRequest; onDecided?: (r: OperatorAccessRequest, text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const actions = done ? [] : operatorActions(r);
  const warnings = emailWarnings(r);
  // Approving a recall another company runs is refused, so Approve is off and the reason sits beside it.
  const blocked = !!r.approved_elsewhere;
  const reasonId = `approve-blocked-${r.id}`;

  async function decide(decision: Decision) {
    setBusy(true);
    setProblem(null);
    try {
      const answer = await sendJson(OperatorAccessRequest, "POST", `/v1/operator/access/${r.id}`, { decision, note: null });
      setDone(true);
      setConfirming(false);
      onDecided?.(r, decisionOutcome(decision, answer.product));
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="grid gap-2 rounded-lg border border-line bg-surface p-4" data-operator-request={r.id}>
      {/* From sm the status stays top right whatever the title's length; on a phone it sits under the title, so the
          title keeps the card's width. */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
        <div className="min-w-0 flex-1">
          <p className="break-words font-semibold text-ink">{r.firm_name}</p>
          <p className="break-words text-sm text-ink">{recallLabel(r)}</p>
        </div>
        <span className={accessBadgeClass(r.status)} data-access-status={r.status}>
          {accessStatusLabel(r.status)}
        </span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-ink-muted">Requested by</dt>
        <dd className="[overflow-wrap:anywhere]" data-requested-by>
          {r.requester_email ?? requesterLabel(r.requested_by)}
        </dd>
        <dt className="text-ink-muted">Requested on</dt>
        <dd>{formatDateTime(r.created_at)}</dd>
        <dt className="text-ink-muted">Plan</dt>
        <dd data-firm-plan>
          {planLabel(r.firm_plan)}, {formatCount(r.firm_active_products ?? 0)} active {(r.firm_active_products ?? 0) === 1 ? "product" : "products"}
        </dd>
        <dt className="text-ink-muted">Company in the notice</dt>
        <dd>{r.notice_company ?? r.typed_company ?? "Not named"}</dd>
      </dl>
      {warnings.length ? (
        <ul className="grid gap-1 rounded-md border border-warn/40 bg-warn-soft px-3 py-2 text-sm font-medium text-warn-ink" data-email-warning>
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      <p className={`text-sm ${r.domain_matches ? "text-success-ink" : "text-warn-ink"}`} data-domain-hint>
        {domainHint(r)}
      </p>
      {r.approved_elsewhere ? (
        <p className="text-sm text-warn-ink" data-approved-elsewhere>
          {r.approved_elsewhere} already runs this recall.
        </p>
      ) : null}
      {r.typed && r.typed_notice ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-ink-muted">Read the typed notice</summary>
          <p className="mt-2 whitespace-pre-wrap break-words">{r.typed_notice}</p>
        </details>
      ) : null}
      {r.note ? <p className="text-sm">Note: {r.note}</p> : null}
      {actions.length ? (
        <div className="flex flex-wrap items-center gap-2">
          {actions.map((a) => {
            const off = a === "approved" && blocked;
            return (
              <button
                key={a}
                type="button"
                disabled={busy || off}
                aria-describedby={off ? reasonId : undefined}
                onClick={() => (a === "revoked" ? setConfirming(true) : void decide(a))}
                className={`rounded-md px-3 py-1.5 text-sm font-semibold disabled:opacity-60 ${a === "approved" ? "bg-accent text-bg hover:opacity-90" : "border border-line-strong hover:border-ink"}`}
                data-operator-action={a}
              >
                {OPERATOR_ACTION_LABEL[a]}
              </button>
            );
          })}
          {blocked && actions.includes("approved") ? (
            <p id={reasonId} className="text-sm text-warn-ink" data-approve-blocked>
              {APPROVED_ELSEWHERE_REASON}
            </p>
          ) : null}
        </div>
      ) : null}
      {confirming ? <RevokeConfirm busy={busy} onConfirm={() => void decide("revoked")} onCancel={() => setConfirming(false)} /> : null}
      {problem ? <ProblemAlert problem={problem} /> : null}
    </article>
  );
}
