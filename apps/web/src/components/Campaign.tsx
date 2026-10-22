"use client";

import { useRef, useState, type ReactNode } from "react";

import { CampaignCounts, CampaignDraftRequest, CampaignSendRequest, CampaignSummary, RegistrationUpload } from "@/lib/api-types";
import { fetchJson, sendForm, sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  CHANNELS,
  campaignStage,
  channelLabel,
  checkCsvFile,
  notificationRows,
  rejectedRows,
  sampleDrafts,
  sendTotal,
  splitPlaceholder,
  templateCsv,
  type Channel,
  type Stage,
} from "@/lib/campaign";
import { formatCount } from "@/lib/format";

import { ProblemAlert } from "./ProblemAlert";

const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";

const asProblem = (err: unknown): ApiProblem =>
  err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };

const STEP_ORDER: Stage[] = ["upload", "match", "draft", "send"];

function Step({ n, id, title, current, children }: { n: number; id: Stage; title: string; current: Stage; children: ReactNode }) {
  const done = STEP_ORDER.indexOf(current) > STEP_ORDER.indexOf(id) || current === "done";
  return (
    <section
      aria-labelledby={`campaign-${id}`}
      className={`min-w-0 rounded-lg border bg-surface p-4 sm:p-5 ${current === id ? "border-brand border-l-4" : "border-line"}`}
      data-step={id}
      data-current={current === id || undefined}
    >
      <h2 id={`campaign-${id}`} className="section-title flex flex-wrap items-center gap-2">
        <span className="text-ink-muted">{n}.</span>
        {title}
        {done ? <span className="rounded-full border border-success/40 bg-success-soft px-2 py-0.5 text-xs font-medium text-success-ink">Done</span> : null}
      </h2>
      <div className="mt-3 grid gap-3">{children}</div>
    </section>
  );
}

/** Counts and the channel table, readable by every role. */
export function CampaignStatus({ summary }: { summary: CampaignSummary }) {
  const rows = notificationRows(summary.notifications);
  return (
    <div className="grid grid-cols-1 gap-4">
      {/* Same tiles as the fraud and payout pages: four across from lg, values pinned to the bottom of each tile. */}
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {(
          [
            ["Registrations", summary.registrations],
            ["Matched", summary.matched],
            ["Not matched", summary.unmatched],
            ["Claim links", summary.claim_links],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-4">
            <dt className="eyebrow">{label}</dt>
            <dd className="kpi-value mt-auto pt-2 break-words">{formatCount(value)}</dd>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Notices by channel</caption>
          <thead className="border-b border-line bg-panel">
            <tr>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-muted">Channel</th>
              <th scope="col" className="px-3 py-2 text-right text-xs font-medium text-ink-muted">Drafted, not sent</th>
              <th scope="col" className="px-3 py-2 text-right text-xs font-medium text-ink-muted">Marked sent</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.channel} className="border-b border-line last:border-b-0" data-channel-row={r.channel}>
                <th scope="row" className="px-3 py-1.5 text-left font-normal">{channelLabel(r.channel)}</th>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(r.draft)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(r.sent)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Up to three notices as drafted: the notice quotes the recall's hazard and remedy text verbatim. */
export function SampleNotices({ sample }: { sample: CampaignSummary["sample"] }) {
  const drafts = sampleDrafts(sample);
  if (!drafts.length) return null;
  return (
    <section aria-labelledby="campaign-samples" className="grid grid-cols-1 gap-3">
      <h2 id="campaign-samples" className="section-title">
        Sample notices
      </h2>
      <p className="text-sm text-ink-muted">
        Hazard and remedy text is quoted from the recall notice, unchanged. Notices keep a marker where each owner’s
        claim link goes: links work like passwords, so RecallOps never stores them in a notice. A real sender would
        issue each link at delivery time.
      </p>
      <ul className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {drafts.map((d, i) => (
          <li key={i} className="min-w-0 rounded-lg border border-line bg-surface p-4 text-sm" data-sample={d.channel}>
            <p className="eyebrow">
              {channelLabel(d.channel)} · {d.status === "sent" ? "marked sent" : d.status}
            </p>
            <p className="mt-2 font-medium [overflow-wrap:anywhere]">{d.subject}</p>
            <p className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-ink-muted [overflow-wrap:anywhere]">
              {splitPlaceholder(d.body).map((p, j) =>
                p.link ? (
                  <span key={j} className="rounded border border-brand/50 bg-brand-soft px-1 font-mono text-xs text-ink" data-link-placeholder>
                    claim link
                  </span>
                ) : (
                  <span key={j}>{p.text}</span>
                ),
              )}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The campaign workflow for one program (admin): upload, match, draft, simulated send. Each step reports exactly
 * what the API answered; the summary is read again after every step.
 */
export function Campaign({
  programId,
  programStatus,
  initial,
  canAdmin,
  exampleModel,
}: {
  programId: string;
  programStatus: string;
  initial: CampaignSummary;
  canAdmin: boolean;
  exampleModel?: string;
}) {
  const [summary, setSummary] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ step: Stage; problem: ApiProblem } | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [upload, setUpload] = useState<RegistrationUpload | null>(null);
  const [matchCounts, setMatchCounts] = useState<Record<string, number> | null>(null);
  const [draftCounts, setDraftCounts] = useState<Record<string, number> | null>(null);
  const [sendCounts, setSendCounts] = useState<Record<string, number> | null>(null);
  const [channels, setChannels] = useState<Set<Channel>>(new Set(["email"]));
  const [sendChannel, setSendChannel] = useState<string>("");
  const [confirmSend, setConfirmSend] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const stage = campaignStage(summary);
  const rows = notificationRows(summary.notifications);
  const toSend = sendTotal(rows, sendChannel || null);
  const base = `/v1/programs/${programId}`;

  async function refresh() {
    try {
      setSummary(await fetchJson(CampaignSummary, `${base}/campaign`));
    } catch {
      // The step's own answer is already shown; a stale summary is corrected on the next step or reload.
    }
  }

  async function run(step: Stage, label: string, action: () => Promise<void>) {
    setBusy(label);
    setProblem(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setProblem({ step, problem: asProblem(err) });
    } finally {
      setBusy(null);
    }
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([templateCsv(exampleModel)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "registrations-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  const stepProblem = (step: Stage) =>
    problem?.step === step ? (
      <ProblemAlert
        problem={problem.problem}
        sentence={
          problem.problem.status === 409 && step === "draft"
            ? "Notices can be drafted only while the program is open, because each matched owner gets a claim link."
            : problem.problem.status === 422 && step === "upload"
              ? "The file could not be read as a registrations CSV."
              : undefined
        }
      />
    ) : null;

  if (!canAdmin) {
    return (
      <div className="grid grid-cols-1 gap-6">
        <p className="text-sm text-ink-muted">Only an admin can upload registrations, draft notices, or mark them sent.</p>
        <CampaignStatus summary={summary} />
        <SampleNotices sample={summary.sample} />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5">
      <p className="rounded-lg border border-line bg-panel p-3 text-sm text-ink-muted" data-simulated>
        Sending is simulated: no email, letter, or text message leaves RecallOps. Marking notices sent records them for
        the monthly report’s “consumers notified” boxes. Registrations must be synthetic test data.
      </p>

      <CampaignStatus summary={summary} />

      <Step n={1} id="upload" title="Upload owner registrations" current={stage}>
        <p className="text-sm text-ink-muted">
          A CSV with an <code className="font-mono text-xs">external_ref</code> column for each owner, plus any of name,
          email, phone, address, model, serial, date_code, and purchase_date. Uploading the same external_ref again
          updates that owner. Only example.com, example.net, or example.org emails and 555-0100 to 555-0199 phones are
          accepted.
        </p>
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
          <label className="grid min-w-0 flex-1 gap-1 text-xs">
            Registrations file (CSV, up to 5 MB)
            <input ref={fileRef} type="file" accept=".csv,text/csv" className="min-w-0 text-sm file:mr-3 file:rounded-md file:border file:border-line-strong file:bg-surface file:px-3 file:py-1.5 file:text-sm" onChange={() => setFileError(null)} />
          </label>
          <button
            type="button"
            disabled={busy !== null}
            className={PRIMARY}
            data-upload
            onClick={() => {
              const file = fileRef.current?.files?.[0] ?? null;
              const check = checkCsvFile(file);
              if (!check.ok) {
                setFileError(check.reason);
                return;
              }
              const form = new FormData();
              form.append("file", file!);
              void run("upload", "upload", async () => {
                setUpload(await sendForm(RegistrationUpload, `${base}/registrations`, form));
                setMatchCounts(null);
                if (fileRef.current) fileRef.current.value = "";
              });
            }}
          >
            {busy === "upload" ? "Uploading…" : "Upload"}
          </button>
          <button type="button" onClick={downloadTemplate} className={SECONDARY}>
            Download a template
          </button>
        </div>
        {fileError ? (
          <p role="alert" className="text-xs text-danger-ink">
            {fileError}
          </p>
        ) : null}
        {upload ? (
          <div className="grid gap-2 text-sm" role="status" data-upload-result>
            <p>
              <span className="font-medium">{formatCount(upload.accepted)} accepted</span>
              {upload.rejected_total ? `, ${formatCount(upload.rejected_total)} rejected` : ", none rejected"}.
            </p>
            {upload.rejected_total ? (
              <div className="max-h-56 overflow-auto rounded-md border border-warn/40 bg-warn-soft text-xs text-warn-ink">
                <table className="w-full border-collapse">
                  <caption className="sr-only">Rejected rows</caption>
                  <thead>
                    <tr className="border-b border-warn/30">
                      <th scope="col" className="px-2 py-1 text-left font-medium">Row</th>
                      <th scope="col" className="px-2 py-1 text-left font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rejectedRows(upload.rejected).map((r, i) => (
                      <tr key={i} className="border-b border-warn/20 last:border-b-0" data-rejected-row={r.row ?? ""}>
                        <td className="px-2 py-1 tabular-nums">{r.row ?? "—"}</td>
                        <td className="px-2 py-1">{r.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {upload.rejected_total > upload.rejected.length ? (
                  <p className="px-2 py-1">Showing the first {upload.rejected.length} of {formatCount(upload.rejected_total)}.</p>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {stepProblem("upload")}
      </Step>

      <Step n={2} id="match" title="Match owners to the recall" current={stage}>
        <p className="text-sm text-ink-muted">
          Each registration is checked against this program’s identification rules (model, serial range, date code),
          the same checks claims get. Run it again after you upload more owners or edit the rules.
        </p>
        <div>
          <button
            type="button"
            disabled={busy !== null || summary.registrations === 0}
            className={stage === "match" ? PRIMARY : SECONDARY}
            data-match
            onClick={() => void run("match", "match", async () => setMatchCounts((await sendJson(CampaignCounts, "POST", `${base}/campaign/match`, {})).counts))}
          >
            {busy === "match" ? "Matching…" : "Match registrations"}
          </button>
        </div>
        {matchCounts ? (
          <p role="status" className="text-sm" data-match-result>
            {formatCount(matchCounts.matched ?? 0)} matched, {formatCount(matchCounts.unmatched ?? 0)} not matched.
          </p>
        ) : null}
        {stepProblem("match")}
      </Step>

      <Step n={3} id="draft" title="Draft notices" current={stage}>
        <p className="text-sm text-ink-muted">
          One notice per matched owner and channel they can be reached on. Each matched owner also gets their own claim
          link (a new claim in this program). Drafting again never duplicates a notice.
        </p>
        {programStatus !== "open" ? (
          <p className="text-sm text-warn-ink">Open the program before drafting notices.</p>
        ) : null}
        <fieldset className="flex flex-wrap gap-3 text-sm">
          <legend className="sr-only">Channels</legend>
          {CHANNELS.map((c) => (
            <label key={c} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={channels.has(c)}
                onChange={(e) =>
                  setChannels((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.add(c);
                    else next.delete(c);
                    return next;
                  })
                }
              />
              {channelLabel(c)}
            </label>
          ))}
        </fieldset>
        <div>
          <button
            type="button"
            disabled={busy !== null || channels.size === 0 || summary.matched === 0 || programStatus !== "open"}
            className={stage === "draft" ? PRIMARY : SECONDARY}
            data-draft
            onClick={() => {
              const body = CampaignDraftRequest.safeParse({ channels: CHANNELS.filter((c) => channels.has(c)) });
              if (!body.success) return;
              void run("draft", "draft", async () => setDraftCounts((await sendJson(CampaignCounts, "POST", `${base}/campaign/drafts`, body.data)).counts));
            }}
          >
            {busy === "draft" ? "Drafting…" : "Draft notices"}
          </button>
        </div>
        {draftCounts ? (
          <p role="status" className="text-sm" data-draft-result>
            New drafts: {Object.entries(draftCounts).map(([c, n]) => `${channelLabel(c)} ${formatCount(n)}`).join(" · ") || "none"}.
          </p>
        ) : null}
        {stepProblem("draft")}
      </Step>

      <Step n={4} id="send" title="Mark notices sent (simulated)" current={stage}>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <label className="grid gap-1 text-xs">
            Channel
            <select value={sendChannel} onChange={(e) => { setSendChannel(e.target.value); setConfirmSend(false); }} className="rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm">
              <option value="">All channels</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {channelLabel(c)}
                </option>
              ))}
            </select>
          </label>
          {!confirmSend ? (
            <button type="button" disabled={busy !== null || toSend === 0} onClick={() => setConfirmSend(true)} className={stage === "send" ? PRIMARY : SECONDARY} data-send-start>
              Mark {formatCount(toSend)} {toSend === 1 ? "notice" : "notices"} sent
            </button>
          ) : null}
        </div>
        {confirmSend ? (
          <div role="group" aria-label="Confirm simulated send" className="grid gap-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink">
            <p className="font-medium">
              Mark {formatCount(toSend)} {sendChannel ? channelLabel(sendChannel).toLowerCase() : ""} {toSend === 1 ? "notice" : "notices"} as sent?
            </p>
            <p>No message is delivered. They will count as consumers notified this month in the monthly report, and cannot be un-sent.</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy !== null}
                className={PRIMARY}
                data-send-confirm
                onClick={() => {
                  const body = CampaignSendRequest.parse({ channel: sendChannel || null });
                  void run("send", "send", async () => {
                    setSendCounts((await sendJson(CampaignCounts, "POST", `${base}/campaign/send`, body)).counts);
                    setConfirmSend(false);
                  });
                }}
              >
                {busy === "send" ? "Marking…" : "Mark sent"}
              </button>
              <button type="button" onClick={() => setConfirmSend(false)} className={SECONDARY}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {sendCounts ? (
          <p role="status" className="text-sm" data-send-result>
            Marked sent: {Object.entries(sendCounts).map(([c, n]) => `${channelLabel(c)} ${formatCount(n)}`).join(" · ") || "nothing was waiting"}.
          </p>
        ) : null}
        {stepProblem("send")}
      </Step>

      <SampleNotices sample={summary.sample} />
    </div>
  );
}
