"use client";

import { useState } from "react";

import { IssuedLink } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { reissueAvailability, reissuePath } from "@/lib/claim-link";

import { Panel } from "./ClaimPanels";
import { ProblemAlert } from "./ProblemAlert";
import { QrPanel } from "./QrCode";

const PRIMARY = "rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel disabled:opacity-60";

/** Who may receive a reissued link: the email the claim carries, never whoever asks for it (anyone can start a claim
 *  from the product's public link, so a request for "my link" proves nothing). */
export function sendOnlyTo(email: string | null | undefined): string {
  const e = email?.trim();
  return e
    ? `Send the new link only to the email on this claim, ${e}.`
    : "This claim has no email yet, so there is no one to send a new link to. Someone who lost the link to an unsubmitted claim can start a new one from the product’s claim link.";
}

/**
 * Issue a fresh private portal link for one claim, for a claimant whose link was lost, expired, or leaked. The link is
 * a bearer secret: it is shown once, here, held only in this component's memory, and never put in a URL or storage.
 */
export function ReissueLink({
  claimId,
  status,
  canAdmin,
  programStatus = null,
  email = null,
  onDone,
}: {
  claimId: string;
  status: string;
  canAdmin: boolean;
  /** Unknown (null) leaves the decision to the API, which answers 409 for a closed product. */
  programStatus?: string | null;
  /** The claimant's email on the claim, the only place a new link may go. */
  email?: string | null;
  onDone?: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const availability = reissueAvailability(status, canAdmin, programStatus);
  const path = reissuePath(claimId);

  async function reissue() {
    if (!path) return;
    setBusy(true);
    setProblem(null);
    try {
      const issued = await sendJson(IssuedLink, "POST", path, {});
      setLink(issued.url);
      setCopied(false);
      setConfirming(false);
      onDone?.();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Panel id="claim-link" title="Claimant link">
      {!availability.allowed ? (
        <p className="text-sm text-ink-muted" data-reissue-blocked>
          {availability.reason}
        </p>
      ) : (
        <div className="grid gap-3" data-reissue>
          <p className="text-sm text-ink-muted">
            If the claimant lost their private link, or it expired or was shared, issue a new one. The old link stops working at once.
          </p>
          {!confirming ? (
            <div>
              <button type="button" onClick={() => { setConfirming(true); setProblem(null); }} className={SECONDARY} data-reissue-start>
                Issue a new link
              </button>
            </div>
          ) : (
            <div role="group" aria-label="Confirm new claim link" className="grid gap-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink">
              <p className="font-medium">Replace this claim’s link?</p>
              <p>The claimant’s current link will stop working. {sendOnlyTo(email)}</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => void reissue()} disabled={busy} className={PRIMARY} data-reissue-confirm>
                  {busy ? "Issuing…" : "Issue new link"}
                </button>
                <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={SECONDARY}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {link ? <ReissuedLink link={link} claimId={claimId} copied={copied} onCopy={() => void copy()} email={email} /> : null}
          {problem ? (
            <ProblemAlert
              problem={problem}
              sentence={problem.status === 409 ? "A new link cannot be issued for this claim right now." : undefined}
            />
          ) : null}
        </div>
      )}
    </Panel>
  );
}

/** The new link, shown once, with its QR code for a printed letter. */
export function ReissuedLink({
  link,
  claimId,
  copied,
  onCopy,
  email = null,
}: {
  link: string;
  claimId: string;
  copied: boolean;
  onCopy: () => void;
  email?: string | null;
}) {
  return (
    <div className="grid gap-2 rounded-md border border-success/40 bg-success-soft p-3 text-sm text-success-ink" role="status" data-reissued>
      <p className="font-medium">New link issued. It is shown only now.</p>
      <div className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded border border-line bg-surface px-2 py-1 font-mono text-xs text-ink" title={link}>
          {link}
        </code>
        <button type="button" onClick={onCopy} className="shrink-0 rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <QrPanel url={link} claimId={claimId} />
      <p className="text-xs">The link and its QR code work like a password for this one claim. {sendOnlyTo(email)}</p>
    </div>
  );
}
