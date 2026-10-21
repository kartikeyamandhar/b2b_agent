"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { PortalAnswer, PortalView } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { answerGate, isPhotoBusy, photoHint, photoKindLabel, portalPath, type AnswerGate, type PhotoPhase } from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

import { ProblemAlert } from "../ProblemAlert";

import { PhotoCapture } from "./PhotoCapture";

/** The send controls: blocked with a visible reason while the reply photo is on its way or after it failed. */
export function AnswerActions({ gate, busy, onSendWithoutPhoto }: { gate: AnswerGate; busy: boolean; onSendWithoutPhoto: () => void }) {
  return (
    <div className="grid gap-2">
      {gate.reason ? (
        <p id="reply-send-reason" aria-live="polite" className="text-sm text-warn-ink" data-send-blocked>
          {gate.reason}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={busy || !gate.canSend}
        aria-describedby={gate.reason ? "reply-send-reason" : undefined}
        className="min-h-12 rounded-md bg-accent px-4 text-base font-semibold text-bg hover:opacity-90 disabled:opacity-60"
        data-send-answer
      >
        {busy ? "Sending…" : "Send answer"}
      </button>
      {gate.offerWithoutPhoto ? (
        <button type="button" disabled={busy} onClick={onSendWithoutPhoto} className="min-h-12 rounded-md border border-line-strong bg-surface px-4 text-base hover:border-ink disabled:opacity-60" data-send-without-photo>
          Send my answer without the photo
        </button>
      ) : null}
    </div>
  );
}

/** Reply to a needs_info question, with an optional photo; the claim is then checked again. */
export function NeedsInfoReply({ token, program }: { token: string; program: PortalView["program"] }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [photoKind, setPhotoKind] = useState<string | null>(null);
  const [photoPhase, setPhotoPhase] = useState<PhotoPhase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const kinds = [...new Set([...program.required_photos, "other"])];
  const gate = answerGate(photoKind ? photoPhase : null);

  function pickKind(k: string) {
    // A new kind mounts a fresh PhotoCapture, so its phase starts over.
    setPhotoKind(photoKind === k ? null : k);
    setPhotoPhase(null);
  }

  async function send(withoutPhoto = false) {
    if (!answerGate(photoKind ? photoPhase : null, withoutPhoto).canSend) return;
    const parsed = PortalAnswer.safeParse({ message: message.trim() });
    if (!parsed.success || !message.trim()) {
      setError(message.trim() ? "Your reply must be at most 2000 characters." : "Write your answer first.");
      return;
    }
    setBusy(true);
    setError(null);
    setProblem(null);
    try {
      await sendJson(PortalView, "POST", portalPath(token, "answer"), parsed.data);
      router.refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <label htmlFor="reply" className="text-base font-medium">
        Your answer
      </label>
      <textarea
        id="reply"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={4}
        maxLength={2000}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? "reply-error" : undefined}
        className="rounded border border-line-strong bg-surface p-3 text-base"
      />
      {error ? (
        <p id="reply-error" role="alert" className="text-sm text-warn-ink">
          {error}
        </p>
      ) : null}
      <fieldset className="grid gap-2">
        <legend className="text-base font-medium">Add a photo (optional)</legend>
        <div className="flex flex-wrap gap-2">
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={photoKind === k}
              // Switching kind mid-upload would unmount the capture and lose track of its upload.
              disabled={isPhotoBusy(photoPhase)}
              onClick={() => pickKind(k)}
              className={`min-h-12 rounded-md border px-3 text-base ${photoKind === k ? "border-accent bg-accent text-bg" : "border-line-strong"} hover:opacity-90 disabled:opacity-60`}
            >
              {photoKindLabel(k)[0]!.toUpperCase() + photoKindLabel(k).slice(1)}
            </button>
          ))}
        </div>
        {photoKind ? (
          <PhotoCapture
            key={photoKind}
            token={token}
            kind={photoKind}
            hint={photoKind === "other" ? "Add any photo that answers the question." : photoHint(photoKind, program)}
            alreadyUploaded={false}
            onUploaded={() => {}}
            onPhaseChange={setPhotoPhase}
          />
        ) : null}
      </fieldset>
      {problem ? <ProblemAlert problem={problem} sentence={portalSentence(problem.status)} /> : null}
      <AnswerActions gate={gate} busy={busy} onSendWithoutPhoto={() => void send(true)} />
    </form>
  );
}
