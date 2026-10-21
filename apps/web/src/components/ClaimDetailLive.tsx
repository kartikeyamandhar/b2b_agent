"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ClaimDetail, Trace } from "@/lib/api-types";
import { ApiError } from "@/lib/api/problem";
import { duplicateHits, needsReviewer, readingsByPhotoKind } from "@/lib/claim-detail";
import { shortId } from "@/lib/claims";
import { formatDateTime } from "@/lib/format";
import { claimDetailQuery, traceQuery } from "@/lib/queries/claim";

import { AuditTrail, ClaimFacts, DecisionPanel, FraudPanel, Panel, PayoutPanel, PhotoEvidence } from "./ClaimPanels";
import { ProblemAlert } from "./ProblemAlert";
import { ReissueLink } from "./ReissueLink";
import { ReviewerActions, type ActionDone } from "./ReviewerActions";
import { StatusBadge } from "./StatusBadge";

/** How long the page keeps polling after an action the worker must apply. */
const AWAIT_WORKER_MS = 60_000;

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
  const awaitingUntil = useRef(0);
  const [flash, setFlash] = useState<{ text: string; waitingFrom: string | null } | null>(null);

  const claimQ = useQuery({ ...claimDetailQuery(id, () => awaitingUntil.current), initialData: initialClaim, initialDataUpdatedAt: loadedAt });
  const traceQ = useQuery({
    ...traceQuery(id, () => (Date.now() < awaitingUntil.current ? 2_000 : false)),
    initialData: initialTrace,
    initialDataUpdatedAt: loadedAt,
  });
  const claim = claimQ.data;
  const trace = traceQ.data ?? null;

  // The worker applied a queued review: the status moved away from the one the action saw.
  useEffect(() => {
    if (flash?.waitingFrom && claim.status !== flash.waitingFrom) {
      awaitingUntil.current = 0;
      setFlash({ text: `The worker applied the review. The status went from ${flash.waitingFrom} to ${claim.status}.`, waitingFrom: null });
    }
  }, [claim.status, flash]);

  const onDone = useCallback(
    (r: ActionDone) => {
      client.setQueryData(["claim", id], r.updated);
      const waiting = r.appliedByWorker && r.updated.status === r.before;
      if (waiting) awaitingUntil.current = Date.now() + AWAIT_WORKER_MS;
      setFlash({ text: r.message, waitingFrom: waiting ? r.before : null });
      void client.invalidateQueries({ queryKey: ["claim", id] });
      void client.invalidateQueries({ queryKey: ["trace", id] });
    },
    [client, id],
  );

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

      {flash ? (
        <p role="status" className="rounded-lg border border-copper/40 border-l-4 border-l-copper bg-copper-soft px-4 py-3 text-sm" data-flash>
          {flash.text}
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
          <ReviewerActions claim={claim} onDone={onDone} />
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
          </Panel>
        </div>
      </div>
      <AuditTrail entries={claim.audit} />
    </div>
  );
}
