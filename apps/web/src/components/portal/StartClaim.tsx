"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, useSyncExternalStore } from "react";

import { IntakeStarted } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import {
  FORGET_FAILED_TEXT,
  FORGET_LABEL,
  FORGOT_TEXT,
  claimPagePath,
  forgetClaim,
  intakePath,
  isPlausibleToken,
  localStore,
  rememberStartedClaim,
  sessionStore,
  startSentence,
  startedClaim,
} from "@/lib/portal";

import { ProblemAlert } from "../ProblemAlert";

import { LinkNotActive } from "./RecallFacts";

const PRIMARY = "inline-flex min-h-12 items-center justify-center rounded-md bg-accent px-4 text-base font-semibold text-bg hover:opacity-90 disabled:opacity-60";
const SECONDARY = "min-h-12 rounded-md border border-line-strong bg-surface px-4 text-base font-medium text-ink hover:border-ink disabled:opacity-60";
const FORGET = "justify-self-start text-base font-medium text-accent underline underline-offset-2 hover:opacity-80";

export type StartOutcome = { kind: "started"; claimToken: string } | { kind: "inactive" } | { kind: "problem"; problem: ApiProblem };

/**
 * Starts one draft claim from a product link: POST /portal/intake/{token} with an empty JSON body (the API answers
 * 415 to anything that is not JSON, so another site cannot start claims from its visitors' browsers). A 404 means the
 * link was replaced or closed while the page was open.
 */
export async function startClaim(token: string): Promise<StartOutcome> {
  try {
    const started = await sendJson(IntakeStarted, "POST", intakePath(token), {});
    if (!isPlausibleToken(started.claim_token)) {
      return { kind: "problem", problem: { status: 502, title: "Contract Mismatch", detail: "the API returned an unusable claim link", correlationId: null, retryAfterSeconds: null } };
    }
    return { kind: "started", claimToken: started.claim_token };
  } catch (err) {
    const problem: ApiProblem = err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null };
    return problem.status === 404 ? { kind: "inactive" } : { kind: "problem", problem };
  }
}

const noSubscription = () => () => {};

/** The claim this device started from this link, and a way to forget it (on a shared device). The server cannot know
 *  it, so its HTML always offers a new claim and the browser switches to Continue once the page loads. After Forget
 *  the page stops offering it at once, and the note says whether the browser let it go. */
function useStartedClaim(token: string): { remembered: string | null; note: string | null; forget: () => void } {
  const stored = useSyncExternalStore(noSubscription, () => startedClaim(localStore(), token), () => null);
  const [forgotten, setForgotten] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const remembered = stored !== null && stored !== forgotten ? stored : null;
  function forget() {
    if (!remembered) return;
    const ok = forgetClaim(localStore(), sessionStore(), remembered);
    if (ok) setForgotten(remembered);
    setNote(ok ? FORGOT_TEXT : FORGET_FAILED_TEXT);
  }
  return { remembered, note, forget };
}

/** What Forget did, in a live region that is always there, so a screen reader hears it; hidden while empty. */
function ForgetNote({ note }: { note: string | null }) {
  return (
    <p aria-live="polite" className={note ? "text-sm text-ink-muted" : "sr-only"} data-forget-note>
      {note ?? ""}
    </p>
  );
}

/** The buttons, given what the device remembers. Continue is a link, so it works even before any script runs. */
export function StartClaimActions({
  remembered,
  busy,
  problem,
  onStart,
  onForget,
  forgetNote = null,
}: {
  remembered: string | null;
  busy: boolean;
  problem: ApiProblem | null;
  onStart: () => void;
  onForget?: () => void;
  forgetNote?: string | null;
}) {
  return (
    <div className="grid gap-3" data-start-claim>
      {/* Retry-After is said in minutes by the sentence, so the alert's own seconds line is left out. */}
      {problem ? <ProblemAlert problem={{ ...problem, retryAfterSeconds: null }} sentence={startSentence(problem)} showDetail={false} audience="claimant" /> : null}
      {remembered ? (
        <>
          <p className="text-sm text-ink-muted">You already started a claim from this link on this device.</p>
          <Link href={claimPagePath(remembered)} prefetch={false} className={PRIMARY} data-continue-claim>
            Continue your claim
          </Link>
          {onForget ? (
            <button type="button" onClick={onForget} className={FORGET} data-forget-claim>
              {FORGET_LABEL}
            </button>
          ) : null}
          <button type="button" onClick={onStart} disabled={busy} className={SECONDARY} data-start-new>
            {busy ? "Starting…" : "Start a new claim"}
          </button>
          <p className="text-sm text-ink-muted">A new claim leaves the one you already started as it is.</p>
        </>
      ) : (
        <button type="button" onClick={onStart} disabled={busy} className={PRIMARY} data-start-first>
          {busy ? "Starting your claim…" : "Start my claim"}
        </button>
      )}
      <ForgetNote note={forgetNote} />
    </div>
  );
}

/** "Start my claim" on a product link. The new claim's private link is remembered on this device for this product
 *  link, and the claimant lands on it with replace, so Back does not return to a page that would start another. */
export function StartClaim({ token }: { token: string }) {
  const router = useRouter();
  const { remembered, note, forget } = useStartedClaim(token);
  const [busy, setBusy] = useState(false);
  const [inactive, setInactive] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  // A ref, not the busy state, so two taps before the next render still start one claim.
  const starting = useRef(false);

  async function start() {
    if (starting.current) return;
    starting.current = true;
    setBusy(true);
    setProblem(null);
    const outcome = await startClaim(token);
    if (outcome.kind === "started") {
      rememberStartedClaim(localStore(), token, outcome.claimToken);
      router.replace(claimPagePath(outcome.claimToken));
      return; // stays busy until the claim page replaces this one, so a second tap cannot start a second claim
    }
    if (outcome.kind === "inactive") setInactive(true);
    else setProblem(outcome.problem);
    starting.current = false;
    setBusy(false);
  }

  if (inactive) {
    return (
      <div className="grid gap-3">
        <LinkNotActive heading="h2" />
        <RememberedClaim token={token} />
      </div>
    );
  }
  return <StartClaimActions remembered={remembered} busy={busy} problem={problem} onStart={() => void start()} onForget={forget} forgetNote={note} />;
}

/** On a link that is not active: a claim this device already started from it keeps its own private link. */
export function RememberedClaim({ token }: { token: string }) {
  const { remembered, note, forget } = useStartedClaim(token);
  if (!remembered && !note) return null;
  return (
    <div className="grid gap-2 rounded-xl border border-line bg-surface p-5" data-remembered-claim>
      {remembered ? (
        <>
          <p className="text-base">You started a claim from this link on this device. It still works at its own private link.</p>
          <Link href={claimPagePath(remembered)} prefetch={false} className={PRIMARY} data-continue-claim>
            Continue your claim
          </Link>
          <button type="button" onClick={forget} className={FORGET} data-forget-claim>
            {FORGET_LABEL}
          </button>
        </>
      ) : null}
      <ForgetNote note={note} />
    </div>
  );
}
