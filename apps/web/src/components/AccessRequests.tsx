"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AccessRequest, Program, RecallPage, type AccessStatus, type MeFirm } from "@/lib/api-types";
import { fetchJson, sendJson } from "@/lib/api/client";
import { ApiError, isNotOnboarded, type ApiProblem } from "@/lib/api/problem";
import { accessBadgeClass, accessBody, accessStatusLabel, planIsFull, productState, programBody, requestFor, type TypedDraft } from "@/lib/access";
import { formatDate } from "@/lib/format";
import { recallSearch } from "@/lib/program-create";
import { PLAN_FULL_SENTENCE } from "@/lib/program-rules";

import { ProblemAlert } from "./ProblemAlert";

function asProblem(err: unknown): ApiProblem {
  return err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
}

/** Said before asking, when the Free slot is taken by an active product or by a request still waiting. */
export const SLOT_TAKEN_SENTENCE = "Free covers one product at a time, and yours is in use or waiting for review. Upgrade to Growth to ask for another.";

/** A full plan: the sentence, and the way to Billing. */
export function UpgradePrompt({ sentence }: { sentence: string }) {
  return (
    <div role="alert" className="grid gap-1 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink" data-upgrade-prompt>
      <p className="font-medium">{sentence}</p>
      <p>
        <Link href="/app/billing" className="font-medium underline">
          Upgrade under Billing
        </Link>
      </p>
    </div>
  );
}

/** A request before the company named itself and picked a plan (409): the way back to setup. */
export function FinishSetup() {
  return (
    <div role="alert" className="grid gap-1 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink" data-finish-setup>
      <p className="font-medium">Finish setting up your company first: its name and plan.</p>
      <p>
        <Link href="/app/welcome" className="font-medium underline">
          Set up your company
        </Link>
      </p>
    </div>
  );
}

/** An API answer on this page: the setup and plan answers get their own way forward, anything else the usual alert. */
export function AccessProblem({ problem }: { problem: ApiProblem }) {
  if (isNotOnboarded(problem)) return <FinishSetup />;
  // Fixed words for a 402, whatever the API's detail says.
  if (problem.status === 402) return <UpgradePrompt sentence={PLAN_FULL_SENTENCE} />;
  return <ProblemAlert problem={problem} />;
}

/** One request's product: open it, finish it, or make it when the approval could not. */
function ProductLine({ r, busy, onCreate }: { r: AccessRequest; busy: boolean; onCreate: (r: AccessRequest) => void }) {
  const state = productState(r);
  if (state.kind === "open") {
    return (
      <p className="text-sm" data-product-state="open">
        <Link href={state.href} className="font-medium text-accent hover:underline">
          Open product
        </Link>
      </p>
    );
  }
  if (state.kind === "draft") {
    return (
      <p className="text-sm text-ink" data-product-state="draft">
        Not open yet.{" "}
        <Link href={state.href} className="font-medium text-accent hover:underline">
          Open the product
        </Link>{" "}
        to finish setting it up.
      </p>
    );
  }
  if (state.kind === "closed") {
    return (
      <p className="text-sm text-ink-muted" data-product-state="closed">
        The product is closed.{" "}
        <Link href={state.href} className="font-medium text-accent hover:underline">
          Go to the product
        </Link>
      </p>
    );
  }
  if (state.kind === "create") {
    return (
      <button
        type="button"
        disabled={busy}
        onClick={() => onCreate(r)}
        className="mt-1 justify-self-start rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-60"
        data-create-product={r.id}
      >
        Create the product
      </button>
    );
  }
  return null;
}

/** Archive search results: a recall already asked for shows its status, any other offers Request access. `disabled`
 *  while a request is in flight or the plan is full, because every click would answer 402. */
export function RecallResults({
  results,
  requests,
  disabled,
  onRequest,
}: {
  results: RecallPage["items"];
  requests: readonly AccessRequest[];
  disabled: boolean;
  onRequest: (recallId: string) => void;
}) {
  if (!results.length) return null;
  return (
    <ul className="grid gap-2" data-access-results>
      {results.map((recall) => {
        const existing = requestFor(requests, recall.id);
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
              <button type="button" disabled={disabled} onClick={() => onRequest(recall.id)} className="shrink-0 self-start whitespace-nowrap rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-60 sm:self-auto" data-request-recall={recall.id}>
                Request access
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** A company's recall access: its requests with their products, and how to ask for a recall (CPSC archive or a
 *  typed notice). `firm` (GET /v1/me) lets the page say a Free plan is full before a request is refused. */
export function AccessRequests({ status, gated = false, firm = null }: { status: AccessStatus; gated?: boolean; firm?: MeFirm | null }) {
  const router = useRouter();
  const [mode, setMode] = useState<"archive" | "typed">("archive");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RecallPage["items"]>([]);
  const [typed, setTyped] = useState<TypedDraft>({ title: "", company: "", notice: "" });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [productProblem, setProductProblem] = useState<ApiProblem | null>(null);
  const full = planIsFull(firm, status.requests);

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

  async function createProduct(r: AccessRequest) {
    setBusy(true);
    setProductProblem(null);
    try {
      const program = await sendJson(Program, "POST", "/v1/programs", programBody(r));
      router.push(`/app/programs/${program.id}`);
    } catch (err) {
      setProductProblem(asProblem(err));
      setBusy(false);
    }
  }

  return (
    // Left aligned at the main gutter like every other console page; the narrower measure is kept for reading.
    <div className="grid w-full max-w-3xl gap-6" data-access-page>
      <header className="grid gap-2">
        <h1 className="page-title">{gated ? "Request access to your recall" : "Recall access"}</h1>
        <p className="text-ink-muted">
          A company runs a recall on RecallOps only after the platform operator confirms it issued the recall. The operator checks your email
          domain against the notice, or calls the contact number the notice gives. Once approved, your product opens with its own claim link.
        </p>
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
                <ProductLine r={r} busy={busy} onCreate={(x) => void createProduct(x)} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No requests yet. Find your recall below.</p>
        )}
        {productProblem ? <AccessProblem problem={productProblem} /> : null}
      </section>

      <section aria-labelledby="new-request" className="grid gap-3">
        <h2 id="new-request" className="section-title">
          Ask for a recall
        </h2>
        {full ? <UpgradePrompt sentence={SLOT_TAKEN_SENTENCE} /> : null}
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
            <RecallResults results={results} requests={status.requests} disabled={busy || full} onRequest={(recallId) => void request({ recallId })} />
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
            <button type="submit" disabled={busy || full} className="justify-self-start rounded-md bg-accent px-4 py-2 font-semibold text-bg hover:opacity-90 disabled:opacity-60" data-request-typed>
              Request approval
            </button>
          </form>
        )}
        {problem ? <AccessProblem problem={problem} /> : null}
      </section>
    </div>
  );
}
