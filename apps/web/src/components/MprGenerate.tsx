"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { MprManual, MprReport } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { MANUAL_NUMBER_FIELDS, boxLabel, periodLabel } from "@/lib/mpr";

import { ProblemAlert } from "./ProblemAlert";

type Manual = Record<(typeof MANUAL_NUMBER_FIELDS)[number], string> & { homepage_posting: "" | "yes" | "no"; homepage_posting_explanation: string };

const EMPTY: Manual = {
  ...(Object.fromEntries(MANUAL_NUMBER_FIELDS.map((k) => [k, ""])) as Record<(typeof MANUAL_NUMBER_FIELDS)[number], string>),
  homepage_posting: "",
  homepage_posting_explanation: "",
};

/** The MprManual body: only boxes the firm entered; blanks keep the documented defaults. */
export function manualBody(m: Manual): { ok: true; body: MprManual } | { ok: false; error: string } {
  const raw: Record<string, unknown> = {};
  for (const k of MANUAL_NUMBER_FIELDS) {
    const v = m[k].trim();
    if (!v) continue;
    const n = Number(v);
    if (!Number.isInteger(n) || n < 0) return { ok: false, error: `${boxLabel(k)} must be a whole number of 0 or more.` };
    raw[k] = n;
  }
  if (m.homepage_posting) raw.homepage_posting = m.homepage_posting === "yes";
  if (m.homepage_posting_explanation.trim()) raw.homepage_posting_explanation = m.homepage_posting_explanation.trim();
  const parsed = MprManual.safeParse(raw);
  return parsed.success ? { ok: true, body: parsed.data } : { ok: false, error: "Some boxes are not valid for the report." };
}

/** Generate (or regenerate) the MPR for a period (POST .../mpr/generate, admin). */
export function MprGenerate({ programId, period, canGenerate, hasReport }: { programId: string; period: string; canGenerate: boolean; hasReport: boolean }) {
  const router = useRouter();
  const [manual, setManual] = useState<Manual>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);

  if (!canGenerate) return <p className="text-sm text-ink-muted">Only an admin can generate reports.</p>;

  async function generate() {
    const built = manualBody(manual);
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setBusy(true);
    setError(null);
    setProblem(null);
    try {
      await sendJson(MprReport, "POST", `/v1/programs/${programId}/mpr/generate?period=${encodeURIComponent(period)}`, built.body);
      router.refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="grid gap-3 rounded-lg border border-line bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void generate();
      }}
    >
      <details>
        <summary className="cursor-pointer text-sm">Boxes the firm fills in (optional)</summary>
        <p className="mt-1 text-xs text-ink-muted">Leave a box empty to keep its documented default. Values entered here are marked in the report.</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {MANUAL_NUMBER_FIELDS.map((k) => (
            <label key={k} className="grid gap-1 text-xs">
              {boxLabel(k)}
              <input
                inputMode="numeric"
                value={manual[k]}
                onChange={(e) => setManual((m) => ({ ...m, [k]: e.target.value }))}
                className="rounded border border-line-strong bg-surface px-2 py-1 text-sm"
              />
            </label>
          ))}
          <label className="grid gap-1 text-xs">
            Recall posted on the homepage
            <select
              value={manual.homepage_posting}
              onChange={(e) => setManual((m) => ({ ...m, homepage_posting: e.target.value as Manual["homepage_posting"] }))}
              className="rounded border border-line-strong bg-surface px-2 py-1 text-sm"
            >
              <option value="">Default</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
          <label className="grid gap-1 text-xs sm:col-span-2">
            Homepage posting explanation
            <input
              value={manual.homepage_posting_explanation}
              onChange={(e) => setManual((m) => ({ ...m, homepage_posting_explanation: e.target.value }))}
              className="rounded border border-line-strong bg-surface px-2 py-1 text-sm"
            />
          </label>
        </div>
      </details>
      {error ? (
        <p role="alert" className="text-xs text-warn-ink">
          {error}
        </p>
      ) : null}
      {problem ? <ProblemAlert problem={problem} /> : null}
      <div>
        <button type="submit" disabled={busy} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-60 hover:opacity-90" data-mpr-generate>
          {busy ? "Generating…" : `${hasReport ? "Regenerate" : "Generate"} the ${periodLabel(period)} report`}
        </button>
      </div>
    </form>
  );
}
