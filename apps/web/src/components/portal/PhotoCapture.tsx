"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Photo } from "@/lib/api-types";
import { sendForm } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { PhotoError, compressPhoto } from "@/lib/image";
import { photoKindLabel, portalPath, type PhotoPhase } from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

import { ProblemAlert } from "../ProblemAlert";

/** A stalled upload is stopped after this long so the claimant gets Try again instead of a spinner forever. */
export const PHOTO_UPLOAD_TIMEOUT_MS = 45_000;

/**
 * One upload attempt. A timeout (or any abort) becomes a status 0 problem, so the caller lands in the error phase
 * with the prepared bytes still in memory for Try again.
 */
export async function uploadPhoto(token: string, kind: string, blob: Blob, timeoutMs = PHOTO_UPLOAD_TIMEOUT_MS): Promise<void> {
  const form = new FormData();
  form.set("kind", kind);
  form.set("file", blob, blob.type === "image/png" ? `${kind}.png` : `${kind}.jpg`);
  const signal = AbortSignal.timeout(timeoutMs);
  try {
    await sendForm(Photo, portalPath(token, "photos"), form, signal);
  } catch (err) {
    if (signal.aborted) {
      throw new ApiError({
        status: 0,
        title: "Upload Timeout",
        detail: "The upload took too long and was stopped. Your photo is still on this page, so you can try again.",
        correlationId: null,
        retryAfterSeconds: null,
      });
    }
    throw err;
  }
}

/**
 * Camera-first photo input (02-ui.md component 10). The prepared photo stays in memory until the upload succeeds,
 * so a dropped connection loses nothing: "Try again" resends the same bytes. Uploading the same kind replaces it.
 * onPhaseChange reports every transition so the form around it can wait while a photo is on its way.
 */
export function PhotoCapture({
  token,
  kind,
  hint,
  alreadyUploaded,
  onUploaded,
  onPhaseChange,
}: {
  token: string;
  kind: string;
  hint: string;
  alreadyUploaded: boolean;
  onUploaded: (kind: string) => void;
  onPhaseChange?: (phase: PhotoPhase) => void;
}) {
  const inputId = useId();
  const [phase, setPhaseState] = useState<PhotoPhase>(alreadyUploaded ? "done" : "idle");
  const [preview, setPreview] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const blob = useRef<Blob | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  function setPhase(next: PhotoPhase) {
    setPhaseState(next);
    onPhaseChange?.(next);
  }

  async function upload() {
    if (!blob.current) return;
    setPhase("uploading");
    setProblem(null);
    setMessage(null);
    try {
      await uploadPhoto(token, kind, blob.current);
      setPhase("done");
      onUploaded(kind);
    } catch (err) {
      setPhase("error");
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    // A new choice replaces the old one first, so a photo that fails to prepare can never make Try again resend
    // the previous photo (review of PR 9).
    blob.current = null;
    setPreview(null);
    setPhase("preparing");
    setProblem(null);
    setMessage(null);
    try {
      const prepared = await compressPhoto(file);
      blob.current = prepared;
      setPreview(URL.createObjectURL(prepared));
      await upload();
    } catch (err) {
      setPhase("error");
      setMessage(err instanceof PhotoError ? err.message : "This photo could not be prepared. Try taking it again.");
    }
  }

  const label = photoKindLabel(kind);
  const statusText = {
    idle: "",
    preparing: "Preparing the photo…",
    uploading: "Uploading…",
    done: `${label[0]!.toUpperCase()}${label.slice(1)} photo saved.`,
    error: "",
  }[phase];

  return (
    <fieldset className="grid gap-2 rounded-lg border border-line bg-surface p-3" data-photo-kind={kind}>
      <legend className="px-1 text-base font-semibold">{label[0]!.toUpperCase() + label.slice(1)} photo</legend>
      <p className="text-sm">{hint}</p>
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element -- local object URL preview
        <img src={preview} alt={`Preview of your ${label} photo`} className="max-h-60 w-full rounded-lg border border-line bg-surface object-contain" />
      ) : null}
      <p aria-live="polite" className="text-sm font-medium" data-photo-phase={phase}>
        {statusText}
      </p>
      {message ? (
        <p role="alert" className="text-sm text-warn-ink">
          {message}
        </p>
      ) : null}
      {problem ? <ProblemAlert problem={problem} sentence={portalSentence(problem.status)} audience="claimant" /> : null}
      <div className="flex flex-wrap gap-2">
        <label
          htmlFor={inputId}
          className="inline-flex min-h-12 cursor-pointer items-center rounded-md border border-line-strong bg-surface px-4 text-base font-medium text-ink focus-within:outline-2 focus-within:outline-accent hover:border-ink"
        >
          {phase === "done" || preview ? "Retake photo" : "Take or choose a photo"}
          <input
            ref={input}
            id={inputId}
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            className="sr-only"
            disabled={phase === "preparing" || phase === "uploading"}
            onChange={(e) => {
              void onFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        {phase === "error" && blob.current ? (
          // Styled like Retake beside it: border-line all but vanished on the dark surface.
          <button
            type="button"
            onClick={() => void upload()}
            className="min-h-12 rounded-md border border-line-strong bg-surface px-4 text-base font-medium text-ink hover:border-ink"
            data-photo-retry
          >
            Try again
          </button>
        ) : null}
      </div>
    </fieldset>
  );
}
