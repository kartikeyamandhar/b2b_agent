import type { PortalView } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { canChoosePayout, payoutProgress, plainStatus, shouldAutoRefresh } from "@/lib/portal";

import { AutoRefresh } from "./AutoRefresh";
import { NeedsInfoReply } from "./NeedsInfoReply";
import { PayoutChoice } from "./PayoutChoice";

const TONE = {
  neutral: "border-line bg-surface",
  good: "border-success/30 border-l-4 border-l-success bg-success-soft text-success-ink",
  attention: "border-warn/40 border-l-4 border-l-warn bg-warn-soft text-warn-ink",
} as const;

/** The claim status page (02-ui.md /claim/[token]/status). Server-rendered, so reading it needs no JavaScript. */
export function StatusView({ token, view }: { token: string; view: PortalView }) {
  const plain = plainStatus(view);
  const progress = payoutProgress(view.payout_status);
  const messages = view.messages ?? [];
  return (
    <div className="grid gap-5">
      <section aria-labelledby="status-title" className={`rounded-xl border p-5 ${TONE[plain.tone]}`}>
        <h2 id="status-title" className="display-title text-2xl">
          {plain.title}
        </h2>
        <p className="mt-1 text-base">{plain.body}</p>
      </section>

      {view.status === "denied" && view.decision_reasons.length ? (
        <section aria-labelledby="reasons-title">
          <h3 id="reasons-title" className="text-base font-semibold">
            Why
          </h3>
          <ul className="mt-1 list-disc pl-5 text-base">
            {view.decision_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {view.status === "needs_info" ? (
        <section aria-labelledby="question-title" className="grid gap-3">
          <h3 id="question-title" className="text-base font-semibold">
            Our question
          </h3>
          <p className="rounded border border-line bg-panel p-3 text-base" data-pending-question>
            {view.pending_question ?? "Please add any information that helps us confirm your claim."}
          </p>
          <NeedsInfoReply token={token} program={view.program} />
        </section>
      ) : null}

      {view.payout_status ? (
        <section aria-labelledby="payout-title" className="grid gap-3">
          <h3 id="payout-title" className="text-base font-semibold">
            Your refund
          </h3>
          {progress && !(canChoosePayout(view) && view.payout_status === "pending") ? (
            <p className="text-base" data-payout-progress={view.payout_status}>
              {progress}
            </p>
          ) : null}
          {canChoosePayout(view) ? <PayoutChoice token={token} current={view.payout_status} /> : null}
        </section>
      ) : null}

      {messages.length ? (
        <section aria-labelledby="messages-title">
          <h3 id="messages-title" className="text-base font-semibold">
            Messages
          </h3>
          <ol className="mt-2 grid gap-2">
            {messages.map((m, i) => (
              <li key={`${m.at}-${i}`} className={`rounded border p-2 text-base ${m.sender === "claimant" ? "border-line bg-panel" : "border-line-strong"}`}>
                <p className="text-xs text-ink-muted">
                  {m.sender === "claimant" ? "You" : "The firm"} · {formatDateTime(m.at)}
                </p>
                <p className="mt-0.5 whitespace-pre-wrap">{m.text}</p>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <p className="text-sm text-ink-muted">
        <a href="" className="text-accent underline">
          Refresh this page
        </a>{" "}
        to see the latest status.
      </p>
      {shouldAutoRefresh(view.status, 0) ? <AutoRefresh status={view.status} /> : null}
    </div>
  );
}
