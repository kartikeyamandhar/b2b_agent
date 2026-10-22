"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Program } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { focusOnOpen } from "@/lib/focus";
import { intakeReplacePath, posterFor, qrFileName } from "@/lib/products";
import { qrPosterHtml } from "@/lib/qr";

import { ProblemAlert } from "./ProblemAlert";
import { QrPanel, openPrintable } from "./QrCode";

const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";

export const REPLACE_WARNING = "The old link and QR code stop working at once. Reprint any posters.";

/** POST /v1/programs/{id}/intake/replace (admin): the product with its new link. The old link stops at once. */
export async function replaceIntakeLink(programId: string): Promise<Program> {
  const path = intakeReplacePath(programId);
  if (!path) throw new Error("not a product id");
  return sendJson(Program, "POST", path, {});
}

/** The inline confirmation before the link is replaced (no browser dialog); nothing is sent until Replace. Its question
 *  takes focus when it opens, because the Replace the link button that opened it is hidden meanwhile. */
export function ReplaceLinkConfirm({ busy, onConfirm, onCancel }: { busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div role="group" aria-label="Confirm replacing the claim link" className="mt-4 grid gap-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink" data-replace-confirm-group>
      <p ref={focusOnOpen} tabIndex={-1} className="font-medium outline-none">
        Replace this product’s claim link?
      </p>
      <p>{REPLACE_WARNING}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onConfirm} disabled={busy} className={PRIMARY} data-replace-confirm>
          {busy ? "Replacing…" : "Replace the link"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className={SECONDARY}>
          Cancel
        </button>
      </div>
    </div>
  );
}

const asProblem = (err: unknown): ApiProblem =>
  err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };

/**
 * The product's one public claim link (plan v3). Anyone with the link or its QR code sees the product's public facts
 * and starts a claim, which gets its own private link. Copy it, download the code, print a one-page poster, or replace
 * the link (POST /v1/programs/{id}/intake/replace, admin), which stops the old link and its code at once.
 */
export function ProductLinkCard({
  program,
  url: initialUrl,
  companyName = null,
  canAdmin = false,
}: {
  program: Program;
  url: string;
  /** Heads the poster; the poster leaves it out when unknown. */
  companyName?: string | null;
  canAdmin?: boolean;
}) {
  const router = useRouter();
  const [url, setUrl] = useState<string | null>(initialUrl);
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [replaced, setReplaced] = useState(false);
  const [printProblem, setPrintProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function copy() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  }

  function printPoster() {
    if (!url) return;
    setPrintProblem(null);
    let html: string;
    try {
      html = qrPosterHtml(posterFor(program, url, companyName));
    } catch {
      setPrintProblem("This link cannot be drawn as a QR code, so no poster was made. Copy the link instead.");
      return;
    }
    if (!openPrintable(html)) setPrintProblem("The poster opens in a new window: allow pop-ups for this site, then print again.");
  }

  async function replace() {
    setBusy(true);
    setProblem(null);
    try {
      const updated = await replaceIntakeLink(program.id);
      setUrl(updated.intake_url ?? null);
      setReplaced(true);
      setCopied(null);
      setConfirming(false);
      router.refresh();
    } catch (err) {
      setProblem(asProblem(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-line bg-surface p-4 sm:p-5" data-product-link-card>
      {url ? (
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
          <QrPanel url={url} fileName={qrFileName(program.title, program.id)} label={`QR code for the claim link of ${program.title}`} />
          <div className="grid min-w-0 flex-1 content-start gap-3">
            <p className="text-sm text-ink-muted">
              Anyone with this link or its QR code can file a claim for this product. Put it on your recall page, in
              owner notices, and on posters. Each claimant gets their own private link when they start a claim.
            </p>
            <div className="flex min-w-0 items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded border border-line bg-panel px-2 py-1.5 font-mono text-xs text-ink" title={url} data-intake-url>
                {url}
              </code>
              <button type="button" onClick={() => void copy()} className="shrink-0 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-panel" data-copy-link>
                {copied === "yes" ? "Copied" : "Copy"}
              </button>
            </div>
            {copied === "failed" ? (
              <p role="alert" className="text-xs text-warn-ink">
                This browser did not allow copying. Select the link and copy it.
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={printPoster} className={SECONDARY} data-print-poster>
                Print a poster
              </button>
              {canAdmin && !confirming ? (
                <button type="button" onClick={() => { setConfirming(true); setProblem(null); }} className={SECONDARY} data-replace-start>
                  Replace the link
                </button>
              ) : null}
            </div>
            {printProblem ? (
              <p role="alert" className="text-xs text-warn-ink">
                {printProblem}
              </p>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="text-sm text-ink-muted" data-no-link>
          The link was replaced, but this product is not taking claims right now, so there is no link to show.
        </p>
      )}
      {confirming ? <ReplaceLinkConfirm busy={busy} onConfirm={() => void replace()} onCancel={() => setConfirming(false)} /> : null}
      {replaced ? (
        <p role="status" className="mt-3 text-sm text-success-ink" data-link-replaced>
          New link ready. The old link and QR code no longer work, so reprint any posters.
        </p>
      ) : null}
      {problem ? (
        <div className="mt-3">
          <ProblemAlert problem={problem} sentence={problem.status === 409 ? "This product is closed, so it has no claim link to replace." : undefined} />
        </div>
      ) : null}
    </div>
  );
}
