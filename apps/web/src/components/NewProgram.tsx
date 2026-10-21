"use client";

import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Program, RecallPage, type RecallSummary } from "@/lib/api-types";
import { fetchJson, sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { formatCount, formatDate } from "@/lib/format";
import { createBody, noticeHref, recallSearch, type CreateErrors } from "@/lib/program-create";

import { ProblemAlert } from "./ProblemAlert";

const INPUT = "rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm";
const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";

const asProblem = (err: unknown): ApiProblem =>
  err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? (
    <p id={id} className="text-xs text-danger-ink">
      {message}
    </p>
  ) : null;
}

/** One recall in the search results; choosing it selects it for the program. */
export function RecallOption({
  recall,
  selected,
  hasProgram,
  onChoose,
}: {
  recall: RecallSummary;
  selected: boolean;
  hasProgram: boolean;
  onChoose: () => void;
}) {
  const href = noticeHref(recall.url);
  return (
    <li
      className={`rounded-lg border bg-surface p-3 ${selected ? "border-brand ring-1 ring-brand" : "border-line"}`}
      data-recall={recall.id}
      data-selected={selected || undefined}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium [overflow-wrap:anywhere]">{recall.title}</p>
          <p className="mt-1 text-xs text-ink-muted">
            CPSC {recall.source_id} · {formatDate(recall.recall_date)} · {formatCount(recall.units_affected)} units
            {recall.units_status !== "ok" ? " (count uncertain)" : ""}
            {recall.category ? ` · ${recall.category}` : ""}
          </p>
          <p className="mt-1 flex flex-wrap gap-x-3 text-xs">
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                Read the notice on cpsc.gov
              </a>
            ) : null}
            {hasProgram ? <span className="text-warn-ink">Your firm already has a program for this recall.</span> : null}
          </p>
        </div>
        <button type="button" onClick={onChoose} aria-pressed={selected} className={selected ? PRIMARY : SECONDARY}>
          {selected ? "Selected" : "Choose"}
        </button>
      </div>
    </li>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`step-${n}`} className="rounded-lg border border-line bg-surface p-4 sm:p-5">
      <h2 id={`step-${n}`} className="section-title">
        <span className="mr-2 text-ink-muted">{n}.</span>
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/**
 * Create a draft program (POST /v1/programs, admin). Nothing reaches claimants until the program is opened on its
 * settings page, after the extracted rules are reviewed.
 */
export function NewProgram({ existingRecallIds }: { existingRecallIds: readonly string[] }) {
  const router = useRouter();
  const existing = new Set(existingRecallIds);
  const [source, setSource] = useState<"recall" | "manual">("recall");
  const [q, setQ] = useState("");
  const [since, setSince] = useState("");
  const [results, setResults] = useState<RecallPage | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchProblem, setSearchProblem] = useState<ApiProblem | null>(null);
  const [chosen, setChosen] = useState<RecallSummary | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [recallDate, setRecallDate] = useState("");
  const [threshold, setThreshold] = useState("");
  const [errors, setErrors] = useState<CreateErrors>({});
  const [creating, setCreating] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function search() {
    setSearching(true);
    setSearchProblem(null);
    try {
      setResults(await fetchJson(RecallPage, "/v1/recalls", recallSearch(q, since)));
    } catch (err) {
      setSearchProblem(asProblem(err));
    } finally {
      setSearching(false);
    }
  }

  async function create() {
    const built =
      source === "recall"
        ? createBody({ source, recallId: chosen?.id ?? "", threshold })
        : createBody({ source, title, description, recallDate, threshold });
    if (!built.ok) {
      setErrors(built.errors);
      return;
    }
    setErrors({});
    setProblem(null);
    setCreating(true);
    try {
      const program = await sendJson(Program, "POST", "/v1/programs", built.body);
      router.push(`/app/programs/${program.id}/settings?created=1`);
    } catch (err) {
      setProblem(asProblem(err));
      setCreating(false);
    }
  }

  return (
    <div className="grid gap-5">
      <Step n={1} title="Find the recall">
        <div role="radiogroup" aria-label="Where the recall comes from" className="mb-4 inline-flex rounded-md border border-line-strong bg-surface p-0.5 text-sm">
          {(
            [
              ["recall", "Search the CPSC archive"],
              ["manual", "Enter the notice by hand"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={source === value}
              onClick={() => {
                setSource(value);
                setErrors({});
              }}
              className={`rounded px-3 py-1 ${source === value ? "bg-accent font-medium text-bg" : "text-ink-muted hover:text-ink"}`}
            >
              {label}
            </button>
          ))}
        </div>

        {source === "recall" ? (
          <div className="grid gap-3" data-source="recall">
            <form
              role="search"
              className="flex flex-col gap-2 sm:flex-row sm:items-end"
              onSubmit={(e) => {
                e.preventDefault();
                void search();
              }}
            >
              <label className="grid min-w-0 flex-1 gap-1 text-xs">
                Product, firm, or recall number
                <input type="search" value={q} onChange={(e) => setQ(e.target.value)} className={INPUT} placeholder="for example laminator or 27013" />
              </label>
              <label className="grid gap-1 text-xs">
                Recalled on or after
                <input type="date" value={since} onChange={(e) => setSince(e.target.value)} className={INPUT} />
              </label>
              <button type="submit" disabled={searching} className={PRIMARY} data-recall-search>
                {searching ? "Searching…" : "Search"}
              </button>
            </form>
            {searchProblem ? <ProblemAlert problem={searchProblem} onRetry={() => void search()} /> : null}
            {results ? (
              results.items.length === 0 ? (
                <p className="text-sm text-ink-muted">No recall matches. Try fewer words, or enter the notice by hand.</p>
              ) : (
                <>
                  <p className="text-xs text-ink-muted" aria-live="polite">
                    {results.total > results.items.length
                      ? `Showing ${results.items.length} of ${formatCount(results.total)} recalls. Narrow the search to see others.`
                      : `${formatCount(results.items.length)} ${results.items.length === 1 ? "recall" : "recalls"}`}
                  </p>
                  <ul className="grid gap-2">
                    {results.items.map((r) => (
                      <RecallOption key={r.id} recall={r} selected={chosen?.id === r.id} hasProgram={existing.has(r.id)} onChoose={() => setChosen(r)} />
                    ))}
                  </ul>
                </>
              )
            ) : (
              <p className="text-sm text-ink-muted">Search the public CPSC recalls loaded by the nightly feed. An empty search lists the newest.</p>
            )}
            <FieldError id="recall-error" message={errors.recall} />
          </div>
        ) : (
          <div className="grid gap-3" data-source="manual">
            <label className="grid gap-1 text-xs">
              Recall title
              <input value={title} onChange={(e) => setTitle(e.target.value)} className={INPUT} aria-invalid={errors.title ? true : undefined} aria-describedby="title-error" />
            </label>
            <FieldError id="title-error" message={errors.title} />
            <label className="grid gap-1 text-xs">
              Notice text
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={8}
                className={INPUT}
                aria-invalid={errors.description ? true : undefined}
                aria-describedby="description-hint description-error"
              />
            </label>
            <p id="description-hint" className="text-xs text-ink-muted">
              Paste the full notice: models, where they were sold, the hazard, and the remedy. Rules are read from this text.
            </p>
            <FieldError id="description-error" message={errors.description} />
            <label className="grid w-full gap-1 text-xs sm:w-56">
              Recall date (optional)
              <input type="date" value={recallDate} onChange={(e) => setRecallDate(e.target.value)} className={INPUT} aria-describedby="date-error" />
            </label>
            <FieldError id="date-error" message={errors.recallDate} />
            <p className="text-xs text-ink-muted">Programs entered by hand are listed as “Manual recall”.</p>
          </div>
        )}
      </Step>

      <Step n={2} title="Review threshold">
        <label className="grid w-full gap-1 text-xs sm:w-56">
          Approvals above this amount wait for a person (USD)
          <input
            inputMode="decimal"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="Firm default"
            className={INPUT}
            aria-invalid={errors.threshold ? true : undefined}
            aria-describedby="threshold-error"
          />
        </label>
        <FieldError id="threshold-error" message={errors.threshold} />
        <p className="mt-2 text-xs text-ink-muted">You can change it later in the program’s settings.</p>
      </Step>

      <Step n={3} title="Create the draft">
        {source === "recall" && chosen ? (
          <p className="mb-3 text-sm" data-chosen>
            Recall: <span className="font-medium">{chosen.title}</span>
          </p>
        ) : null}
        <p className="text-sm text-ink-muted">
          RecallOps reads the notice and proposes identification and remedy rules. The program starts as a draft: no
          claim links work and nothing reaches claimants until you review the rules and open it.
        </p>
        {problem ? (
          <div className="mt-3">
            <ProblemAlert problem={problem} />
          </div>
        ) : null}
        <button type="button" onClick={() => void create()} disabled={creating} className={`${PRIMARY} mt-4`} data-create-program>
          {creating ? "Reading the notice…" : "Create draft program"}
        </button>
      </Step>
    </div>
  );
}
