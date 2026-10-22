"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { OperatorAccessRequest } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { OPERATOR_ACTION_LABEL, accessStatusLabel, domainHint, operatorActions } from "@/lib/access";
import { formatDateTime } from "@/lib/format";

import { ProblemAlert } from "./ProblemAlert";

/** The platform operator's queue: every organization's requests to run a recall, with the hints to check first. */
export function OperatorQueue({ requests }: { requests: readonly OperatorAccessRequest[] }) {
  const pending = requests.filter((r) => r.status === "requested");
  const decided = requests.filter((r) => r.status !== "requested");
  return (
    <div className="mx-auto grid w-full max-w-4xl gap-6" data-operator-queue>
      <header className="grid gap-2">
        <h1 className="page-title">Organizations asking to run a recall</h1>
        <p className="text-ink-muted">
          Approve an organization only when you are sure it is the company that issued the recall: a work email on the company&rsquo;s domain,
          or a call to the contact number in the notice. One organization at most runs each recall. Revoking stops its console, its claim links,
          and its refunds.
        </p>
      </header>
      <section aria-labelledby="pending" className="grid gap-3">
        <h2 id="pending" className="section-title">
          Waiting for your review ({pending.length})
        </h2>
        {pending.length ? pending.map((r) => <RequestCard key={r.id} r={r} />) : <p className="text-sm text-ink-muted">Nothing is waiting.</p>}
      </section>
      {decided.length ? (
        <section aria-labelledby="decided" className="grid gap-3">
          <h2 id="decided" className="section-title">
            Decided
          </h2>
          {decided.map((r) => (
            <RequestCard key={r.id} r={r} />
          ))}
        </section>
      ) : null}
    </div>
  );
}

export function RequestCard({ r }: { r: OperatorAccessRequest }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const actions = operatorActions(r);

  async function decide(decision: "approved" | "rejected" | "revoked") {
    setBusy(true);
    setProblem(null);
    try {
      await sendJson(OperatorAccessRequest, "POST", `/v1/operator/access/${r.id}`, { decision, note: note.trim() || null });
      router.refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="grid gap-2 rounded-lg border border-line bg-surface p-4" data-operator-request={r.id}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words font-semibold text-ink">{r.firm_name}</p>
          <p className="break-words text-sm text-ink">
            {r.typed ? "Typed notice: " : `Recall ${r.recall_number ?? ""}: `}
            {r.title}
          </p>
        </div>
        <span className="text-xs text-ink-muted">{accessStatusLabel(r.status)}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-ink-muted">Requested by</dt>
        <dd className="break-all">
          {r.requested_by}, {formatDateTime(r.created_at)}
        </dd>
        <dt className="text-ink-muted">Company in the notice</dt>
        <dd>{r.notice_company ?? r.typed_company ?? "Not named"}</dd>
      </dl>
      <p className={`text-sm ${r.domain_matches ? "text-success-ink" : "text-warn-ink"}`} data-domain-hint>
        {domainHint(r)}
      </p>
      {r.approved_elsewhere ? (
        <p className="text-sm text-warn-ink" data-approved-elsewhere>
          {r.approved_elsewhere} already runs this recall, so approving this request is refused.
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
        <div className="grid gap-2">
          <label className="grid gap-1 text-sm">
            Note to the organization (optional)
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} className="rounded border border-line-strong bg-surface px-3 py-2" />
          </label>
          <div className="flex flex-wrap gap-2">
            {actions.map((a) => (
              <button
                key={a}
                type="button"
                disabled={busy}
                onClick={() => void decide(a)}
                className={`rounded-md px-3 py-1.5 text-sm font-semibold disabled:opacity-60 ${a === "approved" ? "bg-accent text-bg hover:opacity-90" : "border border-line-strong hover:border-ink"}`}
                data-operator-action={a}
              >
                {OPERATOR_ACTION_LABEL[a]}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {problem ? <ProblemAlert problem={problem} /> : null}
    </article>
  );
}
