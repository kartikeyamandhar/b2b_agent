"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { PortalAnswer, PortalView } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { photoHint, photoKindLabel, portalPath } from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

import { ProblemAlert } from "../ProblemAlert";

import { PhotoCapture } from "./PhotoCapture";

/** Reply to a needs_info question, with an optional photo; the claim is then checked again. */
export function NeedsInfoReply({ token, program }: { token: string; program: PortalView["program"] }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [photoKind, setPhotoKind] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const kinds = [...new Set([...program.required_photos, "other"])];

  async function send() {
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
        className="rounded border border-line-strong bg-bg p-3 text-base"
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
              onClick={() => setPhotoKind(photoKind === k ? null : k)}
              className={`min-h-12 rounded border px-3 text-base ${photoKind === k ? "border-accent bg-accent text-bg" : "border-line-strong"}`}
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
          />
        ) : null}
      </fieldset>
      {problem ? <ProblemAlert problem={problem} sentence={portalSentence(problem.status)} /> : null}
      <button type="submit" disabled={busy} className="min-h-12 rounded bg-accent px-4 text-base font-semibold text-bg disabled:opacity-60">
        {busy ? "Sending…" : "Send answer"}
      </button>
    </form>
  );
}
