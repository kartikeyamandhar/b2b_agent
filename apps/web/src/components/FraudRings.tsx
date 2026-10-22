"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import type { FraudGraphView } from "@/lib/api-types";
import { shortId } from "@/lib/claims";
import { formatCount } from "@/lib/format";
import { KIND_DASH, LINK_KINDS, filterRings, graphSummary, kindLabel, ringLayout, ringsFromGraph, type Ring } from "@/lib/fraud";

import { StatusBadge } from "./StatusBadge";

const PAGE = 12;

function Dash({ kind }: { kind: string }) {
  return (
    <svg aria-hidden="true" width="28" height="8" className="shrink-0">
      <line x1="1" y1="4" x2="27" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray={KIND_DASH[kind] ?? ""} />
    </svg>
  );
}

/** A small picture of one ring: claims on a circle, one line per shared item, the line style naming its kind. */
export function RingDiagram({ ring }: { ring: Ring }) {
  if (ring.members.length > 10) return null;
  const pos = ringLayout(ring.members.map((m) => m.claimId));
  return (
    <svg viewBox="0 0 160 160" className="h-20 w-20 shrink-0 text-ink sm:h-32 sm:w-32" role="img" aria-label={`${ring.members.length} claims linked by ${ring.kinds.map(kindLabel).join(", ").toLowerCase()}`}>
      {ring.edges.map((e, i) => {
        const a = pos.get(e.a)!;
        const b = pos.get(e.b)!;
        return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="currentColor" strokeOpacity="0.7" strokeWidth="2" strokeDasharray={KIND_DASH[e.kind] ?? ""} />;
      })}
      {ring.members.map((m) => {
        const p = pos.get(m.claimId)!;
        const open = m.status === "flagged" || m.status === "under_review" || m.status === "capped";
        return <circle key={m.claimId} cx={p.x} cy={p.y} r="9" className={open ? "fill-warn-soft stroke-warn" : "fill-surface stroke-line-strong"} strokeWidth="2" />;
      })}
    </svg>
  );
}

function RingCard({ ring, index }: { ring: Ring; index: number }) {
  return (
    <li className="min-w-0 rounded-lg border border-line bg-surface p-4" data-ring={index}>
      <div className="flex gap-3 sm:gap-4">
        <RingDiagram ring={ring} />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            Ring {index + 1} · {ring.members.length} claims
          </p>
          <p className="mt-0.5 text-xs text-ink-muted">Shared: {ring.kinds.map(kindLabel).join(", ") || "no link inside the ring"}</p>
          <ul className="mt-3 grid gap-1.5">
            {ring.members.map((m) => (
              <li key={m.claimId} className="flex flex-wrap items-center gap-2 text-sm">
                <Link href={`/app/claims/${m.claimId}`} className="font-mono text-xs" title={m.claimId}>
                  {shortId(m.claimId)}
                </Link>
                <StatusBadge kind="claim" value={m.status} />
              </li>
            ))}
          </ul>
          {ring.edges.length ? (
            <details className="mt-3 text-xs text-ink-muted">
              <summary className="cursor-pointer">Links ({ring.edges.length})</summary>
              <ul className="mt-1 grid gap-1">
                {ring.edges.map((e, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <Dash kind={e.kind} />
                    <span>
                      <span className="font-mono">{shortId(e.a)}</span> and <span className="font-mono">{shortId(e.b)}</span>: {kindLabel(e.kind).toLowerCase()}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </div>
    </li>
  );
}

/**
 * Fraud rings for one program: who is linked to whom and by what (photo, address, serial, payout destination).
 * Filtering and paging happen in the page; the answer is read once on the server.
 */
export function FraudRings({ graph }: { graph: FraudGraphView }) {
  const rings = useMemo(() => ringsFromGraph(graph), [graph]);
  const summary = graphSummary(rings);
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const visible = filterRings(rings, kinds, onlyOpen);
  const present = LINK_KINDS.filter((k) => summary.byKind[k]);

  const toggle = (k: string) => {
    setKinds((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
    setShown(PAGE);
  };

  return (
    <div className="grid grid-cols-1 gap-6">
      {/* Four across only from lg: between 640 and 1023 px the sidebar leaves each tile too narrow for "3 claims".
          Each tile is a column with its value pinned to the bottom, so a label that wraps never lowers its number. */}
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {(
          [
            ["Rings", formatCount(summary.rings)],
            ["Claims in rings", formatCount(summary.claims)],
            ["Rings awaiting review", formatCount(summary.awaitingReview)],
            ["Largest ring", summary.largest ? `${summary.largest} claims` : "—"],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-4">
            <dt className="eyebrow">{label}</dt>
            <dd className="kpi-value mt-auto pt-2 break-words">{value}</dd>
          </div>
        ))}
      </dl>

      {graph.truncated ? (
        <p className="rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm text-warn-ink" data-truncated>
          This program has more linked claims than one page of the graph holds; the largest rings are shown. Every
          claim’s own page still lists its duplicates.
        </p>
      ) : null}

      {rings.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line-strong/60 bg-surface p-8 text-sm" data-no-rings>
          <p className="display-title text-xl">No fraud rings</p>
          <p className="mt-2 text-ink-muted">No two claims in this program share a photo, address, serial number, or payout destination.</p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter rings by what the claims share">
            {present.map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={kinds.has(k)}
                onClick={() => toggle(k)}
                className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${kinds.has(k) ? "border-accent bg-accent text-bg" : "border-line-strong bg-surface text-ink hover:bg-panel"}`}
                data-kind-filter={k}
              >
                <Dash kind={k} />
                {kindLabel(k)} · {summary.byKind[k]}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-2 text-xs">
              <input type="checkbox" checked={onlyOpen} onChange={(e) => { setOnlyOpen(e.target.checked); setShown(PAGE); }} />
              Only rings with a claim awaiting review
            </label>
          </div>
          <p className="text-xs text-ink-muted" aria-live="polite">
            {visible.length === rings.length ? `${formatCount(rings.length)} rings` : `${formatCount(visible.length)} of ${formatCount(rings.length)} rings`}
          </p>
          {visible.length === 0 ? (
            <p className="text-sm text-ink-muted">No ring matches these filters.</p>
          ) : (
            <ol className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {visible.slice(0, shown).map((r) => (
                <RingCard key={r.key} ring={r} index={rings.indexOf(r)} />
              ))}
            </ol>
          )}
          {visible.length > shown ? (
            <div>
              <button type="button" onClick={() => setShown((n) => n + PAGE)} className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm hover:bg-panel">
                Show more rings
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
