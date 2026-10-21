"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { PortalView } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  EMPTY_DRAFT,
  STEPS,
  clearDraft,
  fieldLabel,
  loadDraft,
  missingPhotos,
  photoHint,
  photoKindLabel,
  portalPath,
  remedyPromise,
  saveDraft,
  submitBody,
  validateStep,
  type DraftFields,
  type FieldErrors,
} from "@/lib/portal";
import { formatDate } from "@/lib/format";
import { portalSentence } from "@/lib/portal-copy";

import { ProblemAlert } from "../ProblemAlert";

import { PhotoCapture } from "./PhotoCapture";

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

const today = () => new Date().toISOString().slice(0, 10);

function Field({
  name,
  value,
  error,
  onChange,
  type = "text",
  hint,
  autoComplete,
  list,
  inputMode,
  optional,
}: {
  name: keyof DraftFields;
  value: string;
  error?: string;
  onChange: (v: string) => void;
  type?: string;
  hint?: string;
  autoComplete?: string;
  list?: string;
  inputMode?: "numeric" | "email" | "tel" | "text";
  optional?: boolean;
}) {
  const id = `f-${name}`;
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-base font-medium">
        {fieldLabel(name)}
        {optional ? <span className="font-normal text-ink-muted"> (optional)</span> : null}
      </label>
      {hint ? (
        <p id={`${id}-hint`} className="text-sm text-ink-muted">
          {hint}
        </p>
      ) : null}
      <input
        id={id}
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        list={list}
        inputMode={inputMode}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`min-h-12 rounded border bg-bg px-3 text-base ${error ? "border-warn" : "border-line-strong"}`}
      />
      {error ? (
        <p id={`${id}-error`} className="text-sm text-warn-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Summary({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid gap-1 text-base">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-2">
          <dt className="text-ink-muted">{k}</dt>
          <dd className="min-w-0 [overflow-wrap:anywhere]">{v || <span className="text-ink-muted">Not given</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The six-step claim form (02-ui.md /claim/[token]). Fields resume from this tab's session; photos from the API. */
export function ClaimStepper({ token, view }: { token: string; view: PortalView }) {
  const router = useRouter();
  const program = view.program;
  const [fields, setFields] = useState<DraftFields>({ ...EMPTY_DRAFT });
  const [step, setStep] = useState(0);
  const [uploaded, setUploaded] = useState<string[]>(view.photos_uploaded ?? []);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [restored, setRestored] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const d = loadDraft(session(), view.claim_id);
    setFields(d.fields);
    setStep(d.step);
    setRestored(true);
  }, [view.claim_id]);

  useEffect(() => {
    if (restored) saveDraft(session(), view.claim_id, fields, step);
  }, [fields, step, restored, view.claim_id]);

  const missing = missingPhotos(program.required_photos, uploaded);
  const current = STEPS[step]!;
  const set = (k: keyof DraftFields) => (v: string) => setFields((f) => ({ ...f, [k]: v }));

  function go(next: number) {
    setErrors({});
    setProblem(null);
    setStep(next);
    requestAnimationFrame(() => heading.current?.focus());
  }

  function onContinue() {
    const e = validateStep(current.id, fields, missing, today());
    setErrors(e);
    if (Object.keys(e).length) {
      requestAnimationFrame(() => document.querySelector<HTMLElement>("[aria-invalid='true'], [data-step-error]")?.focus());
      return;
    }
    go(step + 1);
  }

  async function onSubmit() {
    const e = validateStep("review", fields, missing, today());
    setErrors(e);
    if (Object.keys(e).length) return;
    setSubmitting(true);
    setProblem(null);
    try {
      await sendJson(PortalView, "POST", portalPath(token, "submit"), submitBody(fields));
      clearDraft(session(), view.claim_id);
      router.push(`/claim/${encodeURIComponent(token)}/status`);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
      setSubmitting(false);
    }
  }

  const err = (k: keyof DraftFields) => errors[k];

  return (
    <div className="grid min-w-0 grid-cols-1 gap-5">
      <nav aria-label="Claim steps">
        <p className="text-sm text-ink-muted">
          Step {step + 1} of {STEPS.length}
        </p>
        <ol className="mt-1 grid grid-cols-6 gap-1" aria-hidden="true">
          {STEPS.map((s, i) => (
            <li key={s.id} className={`h-1.5 rounded ${i <= step ? "bg-accent" : "bg-line"}`} />
          ))}
        </ol>
      </nav>

      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold outline-none" data-step={current.id}>
        {current.title}
      </h2>

      {current.id === "product" ? (
        <div className="grid gap-3 text-base">
          <p>This claim is for these models:</p>
          <ul className="flex flex-wrap gap-2">
            {program.models.length ? (
              program.models.map((m) => (
                <li key={m} className="rounded border border-line-strong px-2 py-1 font-mono">
                  {m}
                </li>
              ))
            ) : (
              <li className="text-ink-muted">The notice does not list model numbers.</li>
            )}
          </ul>
          {program.model_label_location ? <p>The model number is {program.model_label_location}.</p> : null}
          <p className="rounded border border-line bg-panel p-3">{remedyPromise(program.remedy_type)}</p>
          <p className="text-sm text-ink-muted">Continue if you have one of these products.</p>
        </div>
      ) : null}

      {current.id === "details" ? (
        <div className="grid gap-4">
          <Field name="model_claimed" value={fields.model_claimed} error={err("model_claimed")} onChange={set("model_claimed")} list="recall-models" hint="Type it exactly as printed on the label." autoComplete="off" />
          <datalist id="recall-models">
            {program.models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <Field name="serial_claimed" value={fields.serial_claimed} error={err("serial_claimed")} onChange={set("serial_claimed")} optional autoComplete="off" />
          <Field name="date_code_claimed" value={fields.date_code_claimed} error={err("date_code_claimed")} onChange={set("date_code_claimed")} optional autoComplete="off" />
          <Field name="quantity" value={fields.quantity} error={err("quantity")} onChange={set("quantity")} type="number" inputMode="numeric" />
        </div>
      ) : null}

      {current.id === "photos" ? (
        <div className="grid gap-4">
          {program.required_photos.map((kind) => (
            <PhotoCapture
              key={kind}
              token={token}
              kind={kind}
              hint={photoHint(kind, program)}
              alreadyUploaded={uploaded.includes(kind)}
              onUploaded={(k) => setUploaded((u) => (u.includes(k) ? u : [...u, k]))}
            />
          ))}
          {errors.photos ? (
            <p role="alert" tabIndex={-1} data-step-error className="text-base text-warn-ink">
              {errors.photos}
            </p>
          ) : null}
        </div>
      ) : null}

      {current.id === "purchase" ? (
        <div className="grid gap-4">
          <Field name="retailer_claimed" value={fields.retailer_claimed} error={err("retailer_claimed")} onChange={set("retailer_claimed")} optional hint="The store or website name." autoComplete="off" />
          <Field name="purchase_date" value={fields.purchase_date} error={err("purchase_date")} onChange={set("purchase_date")} type="date" optional />
        </div>
      ) : null}

      {current.id === "contact" ? (
        <div className="grid gap-4">
          <Field name="name" value={fields.name} error={err("name")} onChange={set("name")} autoComplete="name" />
          <Field name="email" value={fields.email} error={err("email")} onChange={set("email")} type="email" inputMode="email" autoComplete="email" />
          <Field name="phone" value={fields.phone} error={err("phone")} onChange={set("phone")} type="tel" inputMode="tel" autoComplete="tel" optional />
          <Field name="address_line1" value={fields.address_line1} error={err("address_line1")} onChange={set("address_line1")} autoComplete="address-line1" />
          <Field name="city" value={fields.city} error={err("city")} onChange={set("city")} autoComplete="address-level2" />
          <Field name="state" value={fields.state} error={err("state")} onChange={set("state")} autoComplete="address-level1" />
          <Field name="zip" value={fields.zip} error={err("zip")} onChange={set("zip")} inputMode="numeric" autoComplete="postal-code" />
        </div>
      ) : null}

      {current.id === "review" ? (
        <div className="grid gap-4">
          <Summary
            rows={[
              ["Model", fields.model_claimed],
              ["Serial", fields.serial_claimed],
              ["Date code", fields.date_code_claimed],
              ["Quantity", fields.quantity],
              ["Photos", program.required_photos.map((k) => `${photoKindLabel(k)}${uploaded.includes(k) ? " ✓" : " (missing)"}`).join(", ")],
              ["Bought at", fields.retailer_claimed],
              ["Bought on", fields.purchase_date ? formatDate(fields.purchase_date) : ""],
              ["Name", fields.name],
              ["Email", fields.email],
              ["Phone", fields.phone],
              ["Address", [fields.address_line1, fields.city, fields.state, fields.zip].filter(Boolean).join(", ")],
            ]}
          />
          <p className="rounded border border-line bg-panel p-3 text-base">{remedyPromise(program.remedy_type)}</p>
          <p className="text-sm text-ink-muted">You cannot change these answers after you submit.</p>
          {Object.keys(errors).length ? (
            <p role="alert" className="text-base text-warn-ink">
              Some answers need attention: {Object.values(errors).join(" ")}
            </p>
          ) : null}
        </div>
      ) : null}

      {problem ? <ProblemAlert problem={problem} sentence={portalSentence(problem.status)} /> : null}

      <div className="flex gap-3">
        {step > 0 ? (
          <button type="button" onClick={() => go(step - 1)} className="min-h-12 flex-1 rounded border border-line-strong px-4 text-base">
            Back
          </button>
        ) : null}
        {current.id === "review" ? (
          <button type="button" onClick={() => void onSubmit()} disabled={submitting} className="min-h-12 flex-[2] rounded bg-accent px-4 text-base font-semibold text-bg disabled:opacity-60" data-submit>
            {submitting ? "Submitting…" : "Submit claim"}
          </button>
        ) : (
          <button type="button" onClick={onContinue} className="min-h-12 flex-[2] rounded bg-accent px-4 text-base font-semibold text-bg" data-continue>
            Continue
          </button>
        )}
      </div>
    </div>
  );
}
