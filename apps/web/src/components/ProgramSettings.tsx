"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Program, ProgramPatch } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { formatCents } from "@/lib/format";
import {
  DATE_CODE_FORMATS,
  REMEDY_TYPES,
  closeImpact,
  draftChanged,
  draftFromProgram,
  evidenceByField,
  lifecycle,
  lifecycleBlock,
  optionsRequireDestructionPhoto,
  remedyOptionsFor,
  rulesPatch,
  rulesWarnings,
  type DateCodeRow,
  type FieldErrors,
  type RulesDraft,
  type SerialRow,
} from "@/lib/program-rules";
import { remedyLabel } from "@/lib/programs";

import { ProblemAlert } from "./ProblemAlert";

const INPUT = "w-full min-w-0 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm disabled:bg-panel disabled:text-ink-muted";
const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";
const DANGER = "rounded-md border border-danger bg-danger px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60";

const asProblem = (err: unknown): ApiProblem =>
  err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };

const QUOTE_MAX = 160;
const clip = (q: string) => (q.length > QUOTE_MAX ? `${q.slice(0, QUOTE_MAX - 1)}…` : q);

/** The notice quotes behind an extracted value, with a "verbatim in notice" mark; an edit says the quote is stale. */
export function Evidence({ quotes, edited }: { quotes?: readonly string[]; edited: boolean }) {
  if (!quotes?.length) return null;
  return (
    <div className="grid gap-0.5 text-xs" data-evidence>
      {edited ? (
        <p className="text-warn-ink" data-evidence-edited>
          Edited: the notice quote below backed the saved value, not this one.
        </p>
      ) : null}
      {quotes.slice(0, 3).map((q) => (
        <p key={q} className={edited ? "text-ink-muted" : "text-success-ink"}>
          <span aria-hidden="true">{edited ? "○" : "✓"}</span> <span className="sr-only">{edited ? "Previously" : "Verbatim"} in the notice:</span>
          <span aria-hidden="true">In the notice:</span> “{clip(q)}”
        </p>
      ))}
    </div>
  );
}

function Field({ label, hint, error, children, evidence }: { label: string; hint?: string; error?: string; children: ReactNode; evidence?: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1">
      <label className="grid gap-1 text-xs">
        <span className="font-medium text-ink">{label}</span>
        {children}
      </label>
      {hint ? <p className="text-xs text-ink-muted">{hint}</p> : null}
      {evidence}
      {error ? (
        <p role="alert" className="text-xs text-danger-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Card({ id, title, children, note }: { id: string; title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section aria-labelledby={id} className="rounded-lg border border-line bg-surface p-4 sm:p-5">
      <h2 id={id} className="section-title">
        {title}
      </h2>
      {note ? <div className="mt-1 text-sm text-ink-muted">{note}</div> : null}
      <div className="mt-4 grid gap-4">{children}</div>
    </section>
  );
}

const BLANK_SERIAL: SerialRow = { raw: "", prefix: "", start: "", end: "" };
const BLANK_CODE: DateCodeRow = { raw: "", format: "", start: "", end: "" };

/** Editable rows for serial ranges or date codes. */
function Rows<T extends Record<string, string>>({
  label,
  rows,
  columns,
  blank,
  disabled,
  onChange,
  error,
  list,
  evidence,
}: {
  label: string;
  rows: T[];
  columns: readonly { key: keyof T & string; label: string; mono?: boolean }[];
  blank: T;
  disabled: boolean;
  onChange: (rows: T[]) => void;
  error?: string;
  list?: string;
  evidence?: ReactNode;
}) {
  return (
    <fieldset className="grid gap-2" data-rows={label}>
      <legend className="text-xs font-medium text-ink">{label}</legend>
      {rows.length === 0 ? <p className="text-xs text-ink-muted">None.</p> : null}
      {evidence}
      {rows.map((row, i) => (
        <div key={i} className="grid gap-2 rounded-md border border-line p-2 sm:grid-cols-[2fr_1fr_1fr_1fr_auto] sm:items-end">
          {columns.map((c) => (
            <label key={c.key} className="grid min-w-0 gap-1 text-xs text-ink-muted">
              {c.label}
              <input
                value={row[c.key]}
                disabled={disabled}
                list={c.key === "format" ? list : undefined}
                onChange={(e) => onChange(rows.map((r, j) => (j === i ? { ...r, [c.key]: e.target.value } : r)))}
                className={`${INPUT} ${c.mono ? "font-mono" : ""}`}
              />
            </label>
          ))}
          <button type="button" disabled={disabled} onClick={() => onChange(rows.filter((_, j) => j !== i))} className="rounded-md border border-line px-2 py-1.5 text-xs text-danger-ink hover:bg-danger-soft disabled:opacity-60">
            Remove
          </button>
        </div>
      ))}
      <div>
        <button type="button" disabled={disabled} onClick={() => onChange([...rows, { ...blank }])} className="rounded-md border border-line px-2 py-1 text-xs hover:bg-panel disabled:opacity-60">
          Add {label.toLowerCase().replace(/s$/, "")}
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-danger-ink">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

const LIFECYCLE_COPY = {
  open: {
    title: "Open the program",
    body: "Opening starts the recall in RecallOps: you can issue claim links, claimants can file, and the adjudicator decides claims with these rules. Open programs count toward your plan.",
    confirm: "Open program",
  },
  closed: {
    title: "Close the program",
    body: "Closing is permanent: a closed program cannot be reopened. Every claim link stops working at once, so claimants can no longer file, answer a question, add a photo, see a decision, or choose how a refund is paid. You can still decide claims here.",
    confirm: "Close program permanently",
  },
} as const;

/**
 * Lifecycle control: one move at a time, confirmed inline (draft → open → closed). `blocked` says why the move
 * cannot start yet (unsaved rule edits before a close).
 */
export function LifecycleControl({ program, onChanged, blocked = null }: { program: Program; onChanged: (p: Program) => void; blocked?: string | null }) {
  const { next } = lifecycle(program.status);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  if (next === null) {
    return <p className="text-sm text-ink-muted">This program is closed. Its records stay available for reports; open a new program for further work.</p>;
  }
  const copy = LIFECYCLE_COPY[next];
  const impact = next === "closed" ? closeImpact(program.claim_counts) : [];

  async function move() {
    if (next === null || blocked) return;
    setBusy(true);
    setProblem(null);
    try {
      const body = ProgramPatch.parse({ status: next });
      onChanged(await sendJson(Program, "PATCH", `/v1/programs/${program.id}`, body));
      setConfirming(false);
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3" data-lifecycle={next}>
      <p className="text-sm text-ink-muted">{copy.body}</p>
      {impact.length ? (
        <div className="rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink" data-close-impact>
          <p className="font-medium">These claims lose their claim link when you close:</p>
          <ul className="mt-1 list-disc pl-5">
            {impact.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {blocked ? (
        <p role="status" className="text-sm text-warn-ink" data-lifecycle-blocked>
          {blocked}
        </p>
      ) : null}
      {!confirming ? (
        <div>
          <button type="button" onClick={() => setConfirming(true)} disabled={!!blocked} className={next === "closed" ? SECONDARY : PRIMARY} data-lifecycle-start>
            {copy.title}
          </button>
        </div>
      ) : (
        <div role="group" aria-label={copy.title} className={`grid gap-2 rounded-md border p-3 ${next === "closed" ? "border-danger/40 bg-danger-soft" : "border-line bg-panel"}`}>
          <p className="text-sm font-medium">{next === "closed" ? "Close this program? This cannot be undone." : "Open this program now?"}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void move()} disabled={busy || !!blocked} className={next === "closed" ? DANGER : PRIMARY} data-lifecycle-confirm>
              {busy ? "Saving…" : copy.confirm}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={SECONDARY}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {problem ? (
        <ProblemAlert
          problem={problem}
          sentence={problem.status === 402 ? "Your plan does not allow another open program. An administrator can upgrade it under Billing." : undefined}
        />
      ) : null}
      {problem?.status === 402 ? (
        <p className="text-sm">
          <a href="/app/billing">Go to Billing</a>
        </p>
      ) : null}
    </div>
  );
}

/**
 * Program settings (PATCH /v1/programs/{id}, admin): the identification and remedy rules the adjudicator checks,
 * the review threshold, and the lifecycle. Reviewers see the same page read-only.
 */
export function ProgramSettings({ program: initial, canAdmin, created = false }: { program: Program; canAdmin: boolean; created?: boolean }) {
  const router = useRouter();
  const [program, setProgram] = useState(initial);
  const [saved, setSaved] = useState(() => draftFromProgram(initial));
  const [draft, setDraft] = useState<RulesDraft>(saved);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [statusNotice, setStatusNotice] = useState<string | null>(null);

  const { editable } = lifecycle(program.status);
  const disabled = !canAdmin || !editable || busy;
  const dirty = draftChanged(draft, saved);
  const warnings = rulesWarnings(program);
  const evidence = evidenceByField(program);
  const remedyChanged = draft.remedyType !== saved.remedyType;
  const planned = remedyOptionsFor(draft.remedyType, program);
  const options = planned.ok ? planned.options : (program.remedy_rules.options ?? []);
  const destructionByOption = optionsRequireDestructionPhoto(options);
  const ev = (...keys: (keyof RulesDraft)[]) => <Evidence quotes={evidence[keys[0]!]} edited={keys.some((k) => JSON.stringify(draft[k]) !== JSON.stringify(saved[k]))} />;

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const set = <K extends keyof RulesDraft>(key: K, value: RulesDraft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setNotice(null);
  };

  function adopt(p: Program, message: string) {
    setProgram(p);
    const d = draftFromProgram(p);
    setSaved(d);
    setDraft(d);
    setNotice(message);
    router.refresh();
  }

  async function save() {
    const result = rulesPatch(draft, program);
    if (!result.ok) {
      setErrors(result.errors);
      setNotice(null);
      return;
    }
    setErrors({});
    setProblem(null);
    setBusy(true);
    try {
      adopt(await sendJson(Program, "PATCH", `/v1/programs/${program.id}`, result.patch), "Rules saved.");
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  const text = (key: keyof RulesDraft, label: string, hint?: string, mono = false) => (
    <Field label={label} hint={hint} error={errors[key]} evidence={key === "priceMax" ? null : key === "priceMin" ? ev("priceMin", "priceMax") : ev(key)}>
      <input value={draft[key] as string} disabled={disabled} onChange={(e) => set(key, e.target.value as never)} className={`${INPUT} ${mono ? "font-mono" : ""}`} aria-invalid={errors[key] ? true : undefined} />
    </Field>
  );
  const area = (key: keyof RulesDraft, label: string, hint?: string, mono = false, rows = 3) => (
    <Field label={label} hint={hint} error={errors[key]} evidence={ev(key)}>
      <textarea value={draft[key] as string} rows={rows} disabled={disabled} onChange={(e) => set(key, e.target.value as never)} className={`${INPUT} ${mono ? "font-mono" : ""}`} aria-invalid={errors[key] ? true : undefined} />
    </Field>
  );

  return (
    <div className="grid gap-5">
      {created && program.status === "draft" ? (
        <div role="status" className="rounded-lg border border-success/40 bg-success-soft p-4 text-sm text-success-ink" data-created>
          Draft program created. Review the rules RecallOps read from the notice, correct anything it missed, then open
          the program.
        </div>
      ) : null}
      {!canAdmin ? <p className="text-sm text-ink-muted">Only an admin can change rules, the threshold, or the program’s status.</p> : null}
      {warnings.length ? (
        <ul className="grid gap-1 rounded-lg border border-warn/40 bg-warn-soft p-4 text-sm text-warn-ink" data-rules-warnings>
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      <form
        className="grid gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
        aria-label="Program rules"
      >
        <Card
          id="identification-rules"
          title="Identification rules"
          note={program.status === "open" ? "Changes apply to claims decided after you save; earlier decisions stay as they are." : "Claims are matched to the recall with these rules."}
        >
          <div className="grid gap-4 lg:grid-cols-2">
            {area("models", "Model numbers", "One per line, exactly as printed on the label.", true, 4)}
            {area("soldAt", "Sold at", "One retailer or website per line.", false, 4)}
          </div>
          {text("modelLabelLocation", "Where the label is")}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Sold from" error={errors.soldFrom} evidence={ev("soldFrom", "soldTo")}>
              <input type="date" value={draft.soldFrom} disabled={disabled} onChange={(e) => set("soldFrom", e.target.value)} className={INPUT} />
            </Field>
            <Field label="Sold to" error={errors.soldTo}>
              <input type="date" value={draft.soldTo} disabled={disabled} onChange={(e) => set("soldTo", e.target.value)} className={INPUT} />
            </Field>
            {text("priceMin", "Lowest price (USD)")}
            {text("priceMax", "Highest price (USD)")}
          </div>
          <Rows
            label="Serial ranges"
            rows={draft.serialRanges}
            blank={BLANK_SERIAL}
            disabled={disabled}
            error={errors.serialRanges}
            onChange={(rows) => set("serialRanges", rows)}
            evidence={ev("serialRanges")}
            columns={[
              { key: "raw", label: "As the notice states it" },
              { key: "prefix", label: "Prefix", mono: true },
              { key: "start", label: "First", mono: true },
              { key: "end", label: "Last", mono: true },
            ]}
          />
          <Rows
            label="Date codes"
            rows={draft.dateCodes}
            blank={BLANK_CODE}
            disabled={disabled}
            error={errors.dateCodes}
            onChange={(rows) => set("dateCodes", rows)}
            evidence={ev("dateCodes")}
            list="date-code-formats"
            columns={[
              { key: "raw", label: "As the notice states it" },
              { key: "format", label: "Format" },
              { key: "start", label: "First", mono: true },
              { key: "end", label: "Last", mono: true },
            ]}
          />
          <datalist id="date-code-formats">
            {DATE_CODE_FORMATS.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
        </Card>

        <Card id="remedy-rules" title="Remedy">
          <Field label="Remedy" error={errors.remedyType} evidence={ev("remedyType")}>
            <select value={draft.remedyType} disabled={disabled} onChange={(e) => set("remedyType", e.target.value)} className={`${INPUT} sm:w-64`}>
              <option value="">Not stated in the notice</option>
              {REMEDY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {remedyLabel(t)}
                </option>
              ))}
            </select>
          </Field>
          {area(
            "destructionInstruction",
            "Destruction instruction",
            destructionByOption
              ? "Shown to claimants on the photo step, and checked against their destruction photo. A destruction photo stays required while a remedy option below lists it, even if you clear this field."
              : "Shown to claimants on the photo step, and checked against their destruction photo.",
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            {text("registrationUrl", "Registration address")}
            {text("contact", "Contact")}
          </div>
          {options.length ? (
            <div className="text-xs text-ink-muted" data-remedy-options>
              <p className="font-medium text-ink">{remedyChanged && planned.ok && planned.changed ? "Remedy options after you save" : "Remedy options from the notice"}</p>
              <ul className="mt-1 list-disc pl-5">
                {options.map((o, i) => (
                  <li key={`${o.type}-${i}`}>
                    {remedyLabel(o.type)}
                    {(o.proof_required ?? []).length ? ` · proof: ${(o.proof_required ?? []).join(", ").replace(/_/g, " ")}` : ""}
                    {o.payout?.method ? ` · paid by ${o.payout.method.replace(/_/g, " ")}` : ""}
                  </li>
                ))}
              </ul>
              <p className="mt-1">
                Claims take their remedy from the first option. Changing the remedy rebuilds the options for it and keeps
                every proof they ask for. The notice quotes behind each rule are kept as extracted.
              </p>
            </div>
          ) : null}
        </Card>

        <Card id="review-threshold" title="Review threshold">
          <Field label="Approvals above this amount wait for a person (USD)" hint={`Saved: ${formatCents(program.value_threshold_cents)}.`} error={errors.threshold}>
            <input inputMode="decimal" value={draft.threshold} disabled={disabled} onChange={(e) => set("threshold", e.target.value)} className={`${INPUT} sm:w-48`} aria-invalid={errors.threshold ? true : undefined} />
          </Field>
        </Card>

        {canAdmin && editable ? (
          <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center gap-3 border-t border-line bg-paper/95 px-4 py-3 sm:mx-0 sm:rounded-lg sm:border sm:bg-surface">
            <button type="submit" disabled={busy || !dirty} className={PRIMARY} data-save-rules>
              {busy ? "Saving…" : "Save changes"}
            </button>
            <button type="button" disabled={busy || !dirty} onClick={() => { setDraft(saved); setErrors({}); }} className={SECONDARY}>
              Discard
            </button>
            <p className="text-xs text-ink-muted" aria-live="polite">
              {notice ?? (dirty ? "Unsaved changes." : "No unsaved changes.")}
            </p>
          </div>
        ) : null}
        {problem ? <ProblemAlert problem={problem} /> : null}
      </form>

      {canAdmin ? (
        <Card id="lifecycle" title="Status">
          <LifecycleControl
            program={program}
            blocked={lifecycleBlock(lifecycle(program.status).next, dirty)}
            onChanged={(p) => {
              // A status change leaves the rules alone, so unsaved edits in the form are kept.
              setProgram(p);
              setStatusNotice(p.status === "open" ? "Program opened. You can now issue claim links from the overview." : "Program closed.");
              router.refresh();
            }}
          />
          {statusNotice ? (
            <p role="status" className="text-sm text-success-ink" data-status-notice>
              {statusNotice}
            </p>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
