// Agent trace viewer (02-ui.md component 6, docs/contracts/trace.md). One row per agent_steps row, the run's use
// against its caps, and the decision's citations marked on the steps they point at. Presentational.
import Link from "next/link";

import type { Trace, TraceStep } from "@/lib/api-types";
import { capUsage, describeResult, isPureCode, resultId, splitCitations, stopReason } from "@/lib/claim-detail";
import { formatDateTime, formatUsdCost } from "@/lib/format";
import { graphLabel } from "@/lib/status";

import { StatusBadge } from "./StatusBadge";

/**
 * Only steps that call a model count toward the step cap, so a rules-only run of seven steps reads "0 of 8"; the
 * label says what is counted. Costs keep their unit in the label, like the time, at two to four decimals.
 */
const CAP_LABEL: Record<string, string> = { Steps: "Steps counted toward the cap" };
const USD_PLAIN = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const capValue = (label: string, v: string) => (label === "Cost (USD)" && v.trim() && Number.isFinite(Number(v)) ? USD_PLAIN.format(Number(v)) : v);

function CapsBar({ trace }: { trace: Trace }) {
  return (
    <dl className="grid gap-3 sm:grid-cols-3" aria-label="Run use against its caps">
      {capUsage(trace).map((c) => (
        <div key={c.label} data-cap={c.label}>
          <dt className="text-xs text-ink-muted">{CAP_LABEL[c.label] ?? c.label}</dt>
          <dd className="text-sm tabular-nums">
            {capValue(c.label, c.used)} <span className="text-ink-muted">of {capValue(c.label, c.max)}</span>
          </dd>
          <div
            className="mt-1 h-1.5 rounded bg-line"
            role="meter"
            aria-label={`${c.label} used`}
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={c.ratio ?? 0}
          >
            <div className={`h-1.5 rounded ${c.ratio !== null && c.ratio >= 1 ? "bg-warn" : "bg-brand"}`} style={{ width: `${Math.round((c.ratio ?? 0) * 100)}%` }} />
          </div>
        </div>
      ))}
    </dl>
  );
}

function RunBanner({ trace, claimId }: { trace: Trace; claimId: string }) {
  const reason = stopReason(trace);
  switch (trace.run.status) {
    case "running":
      return <p role="status" className="text-sm text-accent">The run is in progress. This page refreshes every 1.5 seconds until it finishes.</p>;
    case "waiting_human":
      return (
        <p className="rounded border border-warn/50 border-l-4 border-l-warn bg-warn-soft p-2 text-sm text-warn-ink">
          The run is paused for a reviewer.{" "}
          <Link href={`/app/claims/${claimId}`} className="underline">
            Review the claim
          </Link>
          ; the worker resumes this same run with the decision.
        </p>
      );
    case "capped":
      return (
        <p className="rounded border border-warn/50 border-l-4 border-l-warn bg-warn-soft p-2 text-sm text-warn-ink">
          The run stopped at a cap{reason ? `: ${reason}` : ""}. A person decides the claim.
        </p>
      );
    case "failed":
      return (
        <p className="rounded border border-warn/50 border-l-4 border-l-warn bg-warn-soft p-2 text-sm text-warn-ink">
          The run failed{reason ? `: ${reason}` : ""}. A person decides the claim.
        </p>
      );
    case "done":
      return null;
    default:
      return <p className="text-sm text-ink-muted">Run status &quot;{trace.run.status}&quot; is not recognized by this console.</p>;
  }
}

function Json({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-ink-muted">{label}</summary>
      <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-panel p-2 font-mono text-xs">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function Step({ step, cited }: { step: TraceStep; cited: ReadonlySet<string> }) {
  const rid = resultId(step);
  const isCited = rid !== null && cited.has(rid);
  const r = step.result;
  const failed = !!r && (typeof r.error === "string" || r.capped !== undefined);
  const summary = step.tool && step.tool !== "llm" ? describeResult(step.tool, r) : null;
  return (
    <li
      id={`step-${step.idx}`}
      data-step={step.idx}
      data-cited={isCited || undefined}
      className={`scroll-mt-4 rounded-lg border bg-surface px-4 py-3 ${failed ? "border-danger/30 border-l-4 border-l-danger bg-danger-soft" : isCited ? "border-brand/50 border-l-4 border-l-brand" : "border-line"}`}
    >
      {rid ? <span id={`result-${rid}`} className="block scroll-mt-4" /> : null}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-ink-muted">#{step.idx}</span>
        <span className="font-medium">{step.node}</span>
        {step.tool && step.tool !== step.node ? <span className="rounded border border-line bg-surface px-1 font-mono">{step.tool}</span> : null}
        {rid ? <span className="font-mono text-ink-muted">{rid}</span> : null}
        {isCited ? <span className="rounded-full border border-brand/60 bg-brand-soft px-2 text-ink">cited by the decision</span> : null}
        {step.counted ? <span className="rounded border border-line bg-surface px-1 text-ink-muted">counts toward the step cap</span> : null}
        <span className="ml-auto tabular-nums text-ink-muted">
          {isPureCode(step) ? (
            <>pure code{step.latency_ms > 0 ? ` · ${step.latency_ms} ms` : ""}</>
          ) : (
            <>
              {step.latency_ms} ms · {step.tokens_in}/{step.tokens_out} tokens · {formatUsdCost(step.cost_usd)}
              {step.nominal_cost_usd && step.nominal_cost_usd !== step.cost_usd ? ` (nominal ${formatUsdCost(step.nominal_cost_usd)})` : ""}
            </>
          )}
        </span>
      </div>
      {step.thought ? <p className="mt-1 text-sm italic">{step.thought}</p> : null}
      {summary && summary.text !== "See the trace for this result." ? (
        <p className={`mt-1 text-sm ${summary.problem ? "text-warn-ink" : ""}`}>{summary.text}</p>
      ) : failed ? (
        <p className="mt-1 text-sm text-warn-ink">{typeof r?.error === "string" ? `Rejected: ${r.error}` : `Capped: ${String(r?.capped)}`}</p>
      ) : null}
      <div className="mt-1 grid gap-1">
        <Json label="Arguments" value={step.args} />
        <Json label="Result" value={step.result} />
      </div>
      <p className="mt-1 text-xs text-ink-muted">{formatDateTime(step.created_at)}</p>
    </li>
  );
}

export function TraceView({ trace, claimId, cited }: { trace: Trace; claimId: string; cited: readonly string[] }) {
  const citedResults = new Set(splitCitations(cited).results);
  const steps = [...trace.steps].sort((a, b) => a.idx - b.idx);
  const graph = graphLabel(trace.run.graph);
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{graph}</span>
        <StatusBadge kind="run" value={trace.run.status} />
        <span className="text-xs text-ink-muted">
          run <span className="font-mono">{trace.run.run_id.slice(0, 8)}</span> · started {formatDateTime(trace.run.started_at)}
          {trace.run.finished_at ? ` · finished ${formatDateTime(trace.run.finished_at)}` : " · not finished"} · {trace.run.steps}{" "}
          {trace.run.steps === 1 ? "step" : "steps"} · {formatUsdCost(trace.run.cost_usd)} charged
        </span>
      </div>
      <CapsBar trace={trace} />
      <RunBanner trace={trace} claimId={claimId} />
      {steps.length === 0 ? (
        <p className="text-sm text-ink-muted">The run has not written any steps yet.</p>
      ) : (
        <ol className="grid gap-3" aria-label="Run steps">
          {steps.map((s) => (
            <Step key={s.idx} step={s} cited={citedResults} />
          ))}
        </ol>
      )}
    </div>
  );
}
