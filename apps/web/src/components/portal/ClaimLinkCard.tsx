"use client";

import { useState, useSyncExternalStore } from "react";

import { QrCode } from "@/components/QrCode";
import { FORGET_FAILED_TEXT, FORGET_LABEL, FORGOT_TEXT, claimPagePath, claimUrl, forgetClaim, localStore, sessionStore } from "@/lib/portal";

const BUTTON = "min-h-12 rounded-md border border-line-strong bg-surface px-4 text-base font-medium text-ink hover:border-ink";

const noSubscription = () => () => {};

type Note = "copied" | "copy_failed" | "share_failed" | "forgotten" | "forget_failed" | null;

const NOTE_TEXT: Record<Exclude<Note, null>, string> = {
  copied: "Link copied.",
  copy_failed: "This browser did not copy the link. Select the link above and copy it yourself.",
  share_failed: "This browser could not share the link. Copy it instead.",
  forgotten: FORGOT_TEXT,
  forget_failed: FORGET_FAILED_TEXT,
};

/**
 * "Your private claim link" (plan v3): the claim's own link is the only way back to it, so the claimant sees it in
 * full with Copy, Share (where the browser has a share sheet), and a QR code to carry it to another device. The site
 * address is known only in the browser, so the server HTML shows the path and the browser fills in the rest. On a
 * shared device the claimant can forget the claim here (with `claimId`, this tab's unsent form fields go too); the
 * card itself stores nothing.
 */
export function ClaimLinkCard({ token, claimId = null }: { token: string; claimId?: string | null }) {
  const origin = useSyncExternalStore(noSubscription, () => window.location.origin, () => null);
  const canShare = useSyncExternalStore(noSubscription, () => typeof navigator.share === "function", () => false);
  const [note, setNote] = useState<Note>(null);
  const url = origin ? claimUrl(origin, token) : null;

  async function copy() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setNote("copied");
    } catch {
      setNote("copy_failed");
    }
  }

  function forget() {
    setNote(forgetClaim(localStore(), sessionStore(), token, claimId) ? "forgotten" : "forget_failed");
  }

  async function share() {
    if (!url) return;
    setNote(null);
    try {
      await navigator.share({ title: "My recall claim", url });
    } catch (err) {
      // Closing the share sheet is a choice, not a failure.
      if (!(err instanceof DOMException && err.name === "AbortError")) setNote("share_failed");
    }
  }

  return (
    <section aria-labelledby="claim-link-title" className="grid gap-3 rounded-xl border border-line bg-surface p-5" data-claim-link-card>
      <h2 id="claim-link-title" className="text-base font-semibold">
        Your private claim link
      </h2>
      <p className="text-sm">
        This link is the only way back to this claim. Save it, for example by sending it to yourself. Do not share it with anyone else: whoever has it can open your claim.
      </p>
      <p className="rounded border border-line bg-panel p-3 font-mono text-sm select-all [overflow-wrap:anywhere]" data-claim-url>
        {url ?? claimPagePath(token)}
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => void copy()} disabled={!url} className={`${BUTTON} disabled:opacity-60`} data-copy-link>
          Copy link
        </button>
        {canShare ? (
          <button type="button" onClick={() => void share()} disabled={!url} className={`${BUTTON} disabled:opacity-60`} data-share-link>
            Share
          </button>
        ) : null}
        <button type="button" onClick={forget} className={BUTTON} data-forget-claim>
          {FORGET_LABEL}
        </button>
      </div>
      <p aria-live="polite" className="text-sm text-ink-muted" data-link-note>
        {note ? NOTE_TEXT[note] : ""}
      </p>
      <details className="text-base" data-other-device>
        <summary className="cursor-pointer font-medium text-ink">Continue on another device</summary>
        <div className="mt-3 grid justify-items-start gap-2">
          <p className="text-sm text-ink-muted">Scan this code with the other device&apos;s camera to open this claim there.</p>
          {url ? (
            <div className="rounded-md border border-line bg-white p-2">
              <QrCode value={url} label="QR code for your private claim link" />
            </div>
          ) : (
            <p className="text-sm text-ink-muted">The code appears once the page has loaded.</p>
          )}
        </div>
      </details>
    </section>
  );
}
