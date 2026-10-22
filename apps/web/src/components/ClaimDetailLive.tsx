"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import type { ClaimDetail, Trace } from "@/lib/api-types";
import { ApiError } from "@/lib/api/problem";
import {
  FIRST_RUN_WATCH_MS,
  REVIEW_STILL_WAITING,
  REVIEW_WAIT_MS,
  RUN_STILL_WAITING,
  WAIT_POLL_MS,
  duplicateHits,
  needsReviewer,
  pollDeadline,
  readingsByPhotoKind,
  reviewSettled,
  startReviewWait,
  waitsForWorker,
  type ReviewWait,
} from "@/lib/claim-detail";
import { shortId } from "@/lib/claims";
import { formatDateTime } from "@/lib/format";
import { claimDetailQuery, traceQuery } from "@/lib/queries/claim";

import { AuditTrail, ClaimFacts, DecisionPanel, FraudPanel, MessagesPanel, Panel, PayoutPanel, PhotoEvidence } from "./ClaimPanels";
import { ProblemAlert } from "./ProblemAlert";
import { ReissueLink } from "./ReissueLink";
import { ReviewerActions, type ActionDone } from "./ReviewerActions";
import { StatusBadge } from "./StatusBadge";

const CHECK_AGAIN = "rounded-md border border-line-strong bg-surface px-2.5 py-1 text-xs font-medium text-ink hover:border-ink";

/** Fires once at `until` (and again when `until` moves). */
function useExpiry(until: number | null): boolean {
  const [expiredAt, setExpiredAt] = useState<number | null>(null);
  useEffect(() => {
    if (until === null) return;
    const t = setTimeout(() => setExpiredAt(until), Math.max(0, until - Date.now()));
    return () => clearTimeout(t);
  }, [until]);
  return until !== null && expiredAt === until;
}

export function ClaimDetailLive({
  initialClaim,
  initialTrace,
  loadedAt,
  canAdmin = false,
  programStatus = null,
}: {
  initialClaim: ClaimDetail;
  initialTrace: Trace | null;
  loadedAt: number;
  canAdmin?: boolean;
  programStatus?: string | null;
}) {
  const id = initialClaim.id;
  const client = useQueryClient();
  const [flash, setFlash] = useState<string | null>(null);
  // A review queued for the worker: actions wait and the page polls until it is applied (or the window ends).
  const [review, setReview] = useState<ReviewWait | null>(null);
  // A claim with no decided run yet: the page checks for the worker's run for a while after it loads.
  const [runWatchUntil, setRunWatchUntil] = useState(() => loadedAt + FIRST_RUN_WATCH_MS);

  // The query callbacks read the claim and trace from the cache, so they see the response that just arrived.
  const deadline = () =>
    pollDeadline(review?.until ?? 0, runWatchUntil, client.getQueryData<ClaimDetail>(["claim", id]) ?? initialClaim, client.getQueryData<Trace | null>(["trace", id]));
  const claimQ = useQuery({ ...claimDetailQuery(id, deadline), initialData: initialClaim, initialDataUpdatedAt: loadedAt });
  const traceQ = useQuery({
    ...traceQuery(id, () => (Date.now() < deadline() ? WAIT_POLL_MS : false)),
    initialData: initialTrace,
    initialDataUpdatedAt: loadedAt,
  });
  const claim = claimQ.data;
  const trace = traceQ.data ?? null;
  const reviewOver = useExpiry(review?.until ?? null);
  const waitingForRun = waitsForWorker(claim, trace) && claim.run?.status !== "running";
  const runWatchOver = useExpiry(waitingForRun ? runWatchUntil : null);

  // The worker dealt with the queued review: the status moved, or the paused run moved on.
  useEffect(() => {
    if (!review) return;
    const settled = reviewSettled(review, claim);
    if (settled) {
      setReview(null);
      setFlash(settled);
    }
  }, [claim, review]);

  const onDone = useCallback(
    (r: ActionDone) => {
      client.setQueryData(["claim", id], r.updated);
      const waiting = r.appliedByWorker && r.updated.status === r.before;
      setReview(waiting ? startReviewWait(r.updated, Date.now()) : null);
      setFlash(r.message);
      void client.invalidateQueries({ queryKey: ["claim", id] });
      void client.invalidateQueries({ queryKey: ["trace", id] });
    },
    [client, id],
  );

  // Another tab or reviewer already queued a review: wait for it the same way.
  const onAlreadyQueued = useCallback(() => {
    setReview(startReviewWait(claim, Date.now()));
    setFlash("A review is already queued for this claim. This page refreshes until the worker applies it.");
  }, [claim]);

  const checkAgain = (what: "review" | "run") => {
    if (what === "review") setReview((w) => (w ? { ...w, until: Date.now() + REVIEW_WAIT_MS } : w));
    else setRunWatchUntil(Date.now() + FIRST_RUN_WATCH_MS);
    void claimQ.refetch();
    void traceQ.refetch();
  };

  const pollError = claimQ.error instanceof ApiError ? claimQ.error.problem : null;

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="page-title">
          Claim <span className="font-mono">{shortId(claim.id)}</span>
        </h1>
        <StatusBadge kind="claim" value={claim.status} />
        <span className="text-xs text-ink-muted">created {formatDateTime(claim.created_at)}</span>
        <span className="ml-auto flex gap-2 text-xs">
          <Link href={`/app/claims/${claim.id}/trace`} className="rounded-md border border-line-strong bg-surface px-2 py-1 font-medium text-ink hover:border-ink">
            Open trace
          </Link>
          <Link href={`/app/programs/${claim.program_id}/claims`} className="rounded-lg border border-line bg-surface px-2 py-1">
            Claims queue
          </Link>
        </span>
      </div>

      {needsReviewer(claim.status) ? (
        <p className="rounded border border-warn/50 border-l-4 border-l-warn bg-warn-soft px-3 py-2 text-sm text-warn-ink" data-needs-reviewer>
          This claim needs a reviewer.{" "}
          {claim.status === "under_review"
            ? "It was approved but routed to a person before any money moves (over the value threshold, or a payout hold)."
            : claim.status === "capped"
              ? "The checker stopped at a cap or a failure; a person decides."
              : "The checker flagged it for a person."}
        </p>
      ) : null}

      {review && reviewOver ? (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 border-l-4 border-l-warn bg-warn-soft px-4 py-3 text-sm text-warn-ink" data-review-waiting>
          <p className="min-w-0 flex-1">{REVIEW_STILL_WAITING}</p>
          <button type="button" onClick={() => checkAgain("review")} className={CHECK_AGAIN}>
            Check again
          </button>
        </div>
      ) : flash ? (
        <p role="status" className="rounded-lg border border-brand/40 border-l-4 border-l-brand bg-brand-soft px-4 py-3 text-sm" data-flash>
          {flash}
        </p>
      ) : null}
      {pollError ? (
        <div>
          <p className="mb-1 text-xs text-warn-ink">Could not refresh; showing the last loaded claim.</p>
          <ProblemAlert problem={pollError} />
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="grid min-w-0 grid-cols-1 content-start gap-4">
          <ClaimFacts claim={claim} />
        </div>
        <div className="grid min-w-0 grid-cols-1 content-start gap-4">
          <PhotoEvidence claimId={claim.id} photos={claim.photos} readings={readingsByPhotoKind(trace)} />
        </div>
        <div className="order-first grid min-w-0 grid-cols-1 content-start gap-4 lg:order-none">
          <DecisionPanel claimId={claim.id} decision={claim.decision} decidedAt={claim.decided_at} />
          <MessagesPanel messages={claim.messages} />
          <ReviewerActions claim={claim} onDone={onDone} pending={review !== null} onAlreadyQueued={onAlreadyQueued} />
          <PayoutPanel payout={claim.payout} remedy={claim.remedy} />
          <FraudPanel claimId={claim.id} clusters={claim.clusters} hits={duplicateHits(trace)} programId={claim.program_id} />
          <ReissueLink claimId={claim.id} status={claim.status} canAdmin={canAdmin} programStatus={programStatus} onDone={() => void claimQ.refetch()} />
          <Panel id="claim-run" title="Latest run">
            {claim.run ? (
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge kind="run" value={claim.run.status} />
                <span className="text-xs text-ink-muted">
                  {claim.run.graph} · {claim.run.steps} steps · ${claim.run.cost_usd}
                </span>
              </p>
            ) : (
              <p className="text-sm text-ink-muted">Not adjudicated yet.</p>
            )}
            {waitingForRun ? (
              runWatchOver ? (
                <div className="mt-2 grid gap-2 text-xs text-warn-ink" data-run-watch="stopped">
                  <p>{RUN_STILL_WAITING}</p>
                  <div>
                    <button type="button" onClick={() => checkAgain("run")} className={CHECK_AGAIN}>
                      Check again
                    </button>
                  </div>
                </div>
              ) : (
                <p className="mt-2 text-xs text-ink-muted" data-run-watch="polling">
                  Waiting for the worker. This page checks every few seconds.
                </p>
              )
            ) : null}
          </Panel>
        </div>
      </div>
      <AuditTrail entries={claim.audit} />
    </div>
  );
}
