"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AccessRequest, OperatorAccessRequest, Program, RecallPage, type AccessStatus } from "@/lib/api-types";
import { fetchJson, sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { accessBadgeClass, accessBody, accessStatusLabel, requestFor, typedReadyForProgram, type TypedDraft } from "@/lib/access";
import { formatDate } from "@/lib/format";
import { recallSearch } from "@/lib/program-create";

import { ProblemAlert } from "./ProblemAlert";

function asProblem(err: unknown): ApiProblem {
  return err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
}

/** An organization's recall access: its requests and how to ask for a recall (CPSC archive or a typed notice). */
export function AccessRequests({ status, gated = false }: { status: AccessStatus; gated?: boolean }) {
  const router = useRouter();
  const [mode, setMode] = useState<"archive" | "typed">("archive");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RecallPage["items"]>([]);
  const [typed, setTyped] = useState<TypedDraft>({ title: "", company: "", notice: "" });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function search() {
    setBusy(true);
    setProblem(null);
    try {
      setResults((await fetchJson(RecallPage, "/v1/recalls", recallSearch(q, ""))).items);
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  async function request(choice: { recallId: string } | TypedDraft) {
    const body = accessBody(choice);
    if (!body) {
      setProblem({ status: 422, title: "Incomplete", detail: "Give the recall's title, the company, and the notice text.", correlationId: null, retryAfterSeconds: null });
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await sendJson(AccessRequest, "POST", "/v1/access", body);
      router.refresh();
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  // The operator's own organization: a request left open (made before the operator was recognized, by a colleague,
  // or by the 0008 migration for an existing program) is decided here instead of waiting in their own queue.
  async function approveOwn(r: AccessRequest) {
    setBusy(true);
    setProblem(null);
    try {
      await sendJson(OperatorAccessRequest, "POST", `/v1/operator/access/${r.id}`, { decision: "approved", note: null });
      router.refresh();
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  async function createTyped(r: AccessRequest) {
    setBusy(true);
    setProblem(null);
    try {
      const program = await sendJson(Program, "POST", "/v1/programs", {
        manual: { title: r.title, description: r.typed_notice ?? r.title },
        access_id: r.id,
      });
      router.push(`/app/programs/${program.id}/settings`);
    } catch (err) {
      setProblem(asProblem(err));
      setBusy(false);
    }
  }

  return (
    // Left aligned at the main gutter like every other console page; the narrower measure is kept for reading.
    <div className="grid w-full max-w-3xl gap-6" data-access-page>
      <header className="grid gap-2">
        <h1 className="page-title">{gated ? "Request access to your recall" : "Recall access"}</h1>
        {status.operator ? (
          <p className="text-ink-muted" data-operator-intro>
            You are the platform operator: this organization is approved for a recall the moment you ask, unless another organization runs it.
            Requests from other organizations wait in your queue.
          </p>
        ) : (
          <p className="text-ink-muted">
            An organization runs a recall on RecallOps only after the platform operator confirms it is the company that issued it. The operator
            checks your work email domain against the notice, or calls the contact number the notice gives. You can then open the recall and issue
            claim links.
          </p>
        )}
        {status.operator ? (
          <p>
            <Link href="/operator" className="text-accent hover:underline">
              Open the operator queue
            </Link>
          </p>
        ) : null}
      </header>

      <section aria-labelledby="your-requests" className="grid gap-3">
        <h2 id="your-requests" className="section-title">
          Your requests
        </h2>
        {status.requests.length ? (
          <ul className="grid gap-2" data-access-requests>
            {status.requests.map((r) => (
              <li key={r.id} className="grid gap-1 rounded-lg border border-line bg-surface p-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                  <p className="min-w-0 flex-1 break-words font-medium text-ink">{r.title}</p>
                  <span className={`self-start ${accessBadgeClass(r.status)}`} data-access-status={r.status}>
                    {accessStatusLabel(r.status)}
                  </span>
                </div>
                <p className="text-sm text-ink-muted">
                  {r.typed ? "Typed notice" : "CPSC recall"}, requested {formatDate(r.created_at)}
                  {r.decided_at ? `, decided ${formatDate(r.decided_at)}` : ""}
                </p>
                {r.note ? <p className="text-sm text-ink">Operator&rsquo;s note: {r.note}</p> : null}
                {status.operator && r.status === "requested" ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void approveOwn(r)}
                    className="mt-1 justify-self-start rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-60"
                    data-approve-own={r.id}
                  >
                    Approve now
                  </button>
                ) : null}
                {typedReadyForProgram(r) ? (
                  <button type="button" disabled={busy} onClick={() => void createTyped(r)} className="mt-1 justify-self-start rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-60">
                    Create the program
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No requests yet. Find your recall below.</p>
        )}
      </section>

      <section aria-labelledby="new-request" className="grid gap-3">
        <h2 id="new-request" className="section-title">
          Ask for a recall
        </h2>
        {/* Short labels that fit one line each at 390 px; the typed form says when to use it. */}
        <div role="radiogroup" aria-label="Where the recall comes from" className="inline-flex justify-self-start rounded-md border border-line-strong bg-surface p-0.5 text-sm">
          {(
            [
              ["archive", "CPSC archive"],
              ["typed", "Typed notice"],
            ] as const
          ).map(([value, label]) => (
            <button key={value} type="button" role="radio" aria-checked={mode === value} onClick={() => setMode(value)} className={`whitespace-nowrap rounded px-3 py-1.5 ${mode === value ? "bg-accent text-bg" : "text-ink"}`}>
              {label}
            </button>
          ))}
        </div>

        {mode === "archive" ? (
          <div className="grid gap-3">
            <form
              className="flex flex-col gap-2 sm:flex-row"
              onSubmit={(e) => {
                e.preventDefault();
                void search();
              }}
            >
              <label className="sr-only" htmlFor="access-q">
                Search the CPSC archive
              </label>
              <input id="access-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Company, product, or recall number" className="min-w-0 rounded border border-line-strong bg-surface px-3 py-2 sm:flex-1" />
              <button type="submit" disabled={busy} className="shrink-0 rounded-md border border-line-strong px-4 py-2 font-medium hover:border-ink disabled:opacity-60">
                {busy ? "Searching…" : "Search"}
              </button>
            </form>
            {results.length ? (
              <ul className="grid gap-2" data-access-results>
                {results.map((recall) => {
                  const existing = requestFor(status.requests, recall.id);
                  return (
                    <li key={recall.id} className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-3 sm:flex-row sm:items-center sm:justify-between" data-access-result={recall.id}>
                      <div className="min-w-0 sm:flex-1">
                        <p className="break-words font-medium text-ink">{recall.title}</p>
                        <p className="text-sm text-ink-muted">
                          Recall {recall.source_id}, {formatDate(recall.recall_date)}
                        </p>
                      </div>
                      {existing ? (
                        <span className={`self-start sm:self-auto ${accessBadgeClass(existing.status)}`} data-access-status={existing.status}>
                          {accessStatusLabel(existing.status)}
                        </span>
                      ) : (
                        <button type="button" disabled={busy} onClick={() => void request({ recallId: recall.id })} className="shrink-0 self-start whitespace-nowrap rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-60 sm:self-auto" data-request-recall={recall.id}>
                          Request access
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
        ) : (
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void request(typed);
            }}
          >
            <p className="text-sm text-ink-muted">For a recall that is not in the CPSC archive yet: type its notice, and the operator reviews it like any other request.</p>
            <label className="grid gap-1 text-sm">
              Recall title
              <input value={typed.title} onChange={(e) => setTyped({ ...typed, title: e.target.value })} maxLength={300} className="rounded border border-line-strong bg-surface px-3 py-2 text-base" />
            </label>
            <label className="grid gap-1 text-sm">
              Company that issued the recall
              <input value={typed.company} onChange={(e) => setTyped({ ...typed, company: e.target.value })} maxLength={200} className="rounded border border-line-strong bg-surface px-3 py-2 text-base" />
            </label>
            <label className="grid gap-1 text-sm">
              Notice text
              <textarea value={typed.notice} onChange={(e) => setTyped({ ...typed, notice: e.target.value })} rows={6} maxLength={20000} className="rounded border border-line-strong bg-surface p-3 text-base" />
            </label>
            <button type="submit" disabled={busy} className="justify-self-start rounded-md bg-accent px-4 py-2 font-semibold text-bg hover:opacity-90 disabled:opacity-60">
              Request approval
            </button>
          </form>
        )}
        {problem ? <ProblemAlert problem={problem} /> : null}
      </section>
    </div>
  );
}
