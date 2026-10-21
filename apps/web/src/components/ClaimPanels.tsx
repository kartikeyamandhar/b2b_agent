// Claim detail panels (02-ui.md component 5). Presentational: data in, markup out, so they render on the server
// and are tested with react-dom/server.
import Link from "next/link";
import type { ReactNode } from "react";

import type { AuditEntry, ClaimDetail, Cluster, Decision, PayoutView, Photo } from "@/lib/api-types";
import { clusterLabel, photoSrc, splitCitations, type DuplicateHit, type Reading } from "@/lib/claim-detail";
import { shortId } from "@/lib/claims";
import { formatCents, formatDate, formatDateTime } from "@/lib/format";
import { remedyLabel } from "@/lib/programs";

import { StatusBadge } from "./StatusBadge";

export function Panel({ id, title, children, tone }: { id: string; title: string; children: ReactNode; tone?: "attention" }) {
  return (
    <section
      aria-labelledby={id}
      className={`min-w-0 rounded border p-3 ${tone === "attention" ? "border-warn/50 border-l-4 border-l-warn" : "border-line"}`}
    >
      <h2 id={id} className="mb-2 text-sm font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2 py-0.5 text-sm">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

const dash = <span className="text-ink-muted">—</span>;
const val = (v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? dash : v);

export function ClaimFacts({ claim }: { claim: ClaimDetail }) {
  const p = claim.claimant;
  const address = [p.address_line1, [p.city, p.state].filter(Boolean).join(", "), p.zip].filter(Boolean).join(" · ");
  return (
    <Panel id="claim-facts" title="Claimant and claim">
      <dl>
        <Row label="Name">
          {val(p.name)}
          {p.synthetic ? <span className="ml-2 rounded border border-line px-1 text-xs text-ink-muted">synthetic</span> : null}
        </Row>
        <Row label="Email">{val(p.email)}</Row>
        <Row label="Phone">{val(p.phone)}</Row>
        <Row label="Address">{val(address)}</Row>
      </dl>
      <dl className="mt-3 border-t border-line pt-2">
        <Row label="Model">{claim.model_claimed ? <span className="font-mono">{claim.model_claimed}</span> : dash}</Row>
        <Row label="Serial">{claim.serial_claimed ? <span className="font-mono">{claim.serial_claimed}</span> : dash}</Row>
        <Row label="Date code">{claim.date_code_claimed ? <span className="font-mono">{claim.date_code_claimed}</span> : dash}</Row>
        <Row label="Retailer">{val(claim.retailer_claimed)}</Row>
        <Row label="Purchased">{claim.purchase_date ? formatDate(claim.purchase_date) : dash}</Row>
        <Row label="Quantity">{claim.quantity}</Row>
        <Row label="Remedy">{claim.remedy ? remedyLabel(claim.remedy) : dash}</Row>
        <Row label="Refund">{claim.refund_cents === null ? dash : formatCents(claim.refund_cents)}</Row>
        <Row label="Submitted">{formatDateTime(claim.submitted_at)}</Row>
      </dl>
    </Panel>
  );
}

const PHOTO_LABEL: Record<string, string> = { label: "Label", destruction: "Destruction", receipt: "Receipt", other: "Other" };

export function PhotoEvidence({
  claimId,
  photos,
  readings,
}: {
  claimId: string;
  photos: readonly Photo[];
  readings: Record<string, Reading>;
}) {
  return (
    <Panel id="claim-photos" title="Photos and readings">
      {photos.length === 0 ? (
        <p className="text-sm text-ink-muted">No photos were uploaded with this claim.</p>
      ) : (
        <ul className="grid gap-3">
          {photos.map((ph) => {
            const src = photoSrc(ph.url, claimId);
            const label = Object.hasOwn(PHOTO_LABEL, ph.kind) ? PHOTO_LABEL[ph.kind] : ph.kind;
            const reading = Object.hasOwn(readings, ph.kind) ? readings[ph.kind] : undefined;
            return (
              <li key={ph.id} data-photo-kind={ph.kind}>
                <figure className="grid gap-2 sm:grid-cols-[10rem_1fr]">
                  {src ? (
                    <a href={src} target="_blank" rel="noopener" className="block">
                      {/* eslint-disable-next-line @next/next/no-img-element -- bytes come from the authenticated proxy */}
                      <img
                        src={src}
                        alt={`${label} photo submitted with the claim`}
                        loading="lazy"
                        className="h-32 w-full rounded border border-line bg-panel object-contain sm:w-40"
                      />
                    </a>
                  ) : (
                    <div className="grid h-32 place-items-center rounded border border-dashed border-line text-xs text-ink-muted">
                      Photo unavailable
                    </div>
                  )}
                  <figcaption className="text-sm">
                    <p className="font-medium">{label}</p>
                    <p className="text-xs text-ink-muted">
                      Uploaded {formatDateTime(ph.uploaded_at)} · sha256 <span className="font-mono">{ph.sha256.slice(0, 12)}</span>
                    </p>
                    {reading ? (
                      <p className={`mt-1 ${reading.problem ? "text-warn-ink" : ""}`} data-reading={reading.tool}>
                        {reading.text}{" "}
                        <Link href={`/app/claims/${claimId}/trace#step-${reading.idx}`} className="text-xs text-accent underline">
                          step {reading.idx}
                          {reading.resultId ? ` · ${reading.resultId}` : ""}
                        </Link>
                      </p>
                    ) : (
                      <p className="mt-1 text-xs text-ink-muted">No reading of this photo in the latest run.</p>
                    )}
                  </figcaption>
                </figure>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

export function DecisionPanel({ claimId, decision, decidedAt }: { claimId: string; decision: Decision | null; decidedAt: string | null }) {
  if (!decision) {
    return (
      <Panel id="claim-decision" title="Decision">
        <p className="text-sm text-ink-muted">No decision yet.</p>
      </Panel>
    );
  }
  const { results, rules } = splitCitations(decision.cited_rules);
  return (
    <Panel id="claim-decision" title="Decision">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge kind="outcome" value={decision.outcome} />
        <span className="text-xs text-ink-muted">
          by {decision.decided_by ?? "unknown"} · {formatDateTime(decidedAt)}
          {decision.confidence !== null && decision.confidence !== undefined ? ` · confidence ${Math.round(decision.confidence * 100)}%` : ""}
          {decision.routed_to_human ? " · routed to a person" : ""}
        </span>
      </div>
      {decision.reasons.length ? (
        <ul className="mt-2 list-disc pl-5 text-sm">
          {decision.reasons.map((r, i) => (
            <li key={`${i}-${r}`}>{r}</li>
          ))}
        </ul>
      ) : null}
      {decision.cited_rules.length ? (
        <div className="mt-2 text-xs">
          <span className="text-ink-muted">Cites </span>
          <ul className="inline-flex flex-wrap gap-1 align-middle">
            {rules.map((r) => (
              <li key={r} className="rounded border border-line px-1 font-mono">
                {r}
              </li>
            ))}
            {results.map((t) => (
              <li key={t}>
                <Link href={`/app/claims/${claimId}/trace#result-${t}`} className="rounded border border-accent/50 px-1 font-mono text-accent">
                  {t}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}

const METHOD_LABEL: Record<string, string> = { stripe_connect: "Stripe transfer", check_export: "Mailed check", ach_export: "ACH export" };

export function PayoutPanel({ payout, remedy }: { payout: PayoutView | null; remedy: string | null }) {
  if (!payout) {
    const why =
      remedy === "repair" || remedy === "replacement"
        ? `${remedyLabel(remedy)} remedies are not paid out; a reviewer marks them delivered.`
        : "No payout exists for this claim.";
    return (
      <Panel id="claim-payout" title="Payout">
        <p className="text-sm text-ink-muted">{why}</p>
      </Panel>
    );
  }
  const reversed = payout.stripe_reversed_cents ?? 0;
  return (
    <Panel id="claim-payout" title="Payout" tone={payout.status === "held" || reversed > 0 ? "attention" : undefined}>
      <dl>
        <Row label="Status">
          <StatusBadge kind="payout" value={payout.status} />
        </Row>
        <Row label="Amount">
          {formatCents(payout.amount_cents)} {payout.currency.toUpperCase()}
        </Row>
        <Row label="Method">{Object.hasOwn(METHOD_LABEL, payout.method) ? METHOD_LABEL[payout.method] : payout.method}</Row>
        <Row label="Transfer">{payout.stripe_transfer_id ? <span className="font-mono text-xs">{payout.stripe_transfer_id}</span> : dash}</Row>
        {reversed > 0 ? (
          <Row label="Reversed">
            <span className="text-warn-ink">{formatCents(reversed)} reversed; an operator must review it. It is never repaid automatically.</span>
          </Row>
        ) : null}
      </dl>
      {payout.status === "transferred" ? (
        <p className="mt-1 text-xs text-ink-muted">Transferred means Stripe accepted the transfer, not that the bank received it.</p>
      ) : null}
    </Panel>
  );
}

export function FraudPanel({ claimId, clusters, hits }: { claimId: string; clusters: readonly Cluster[]; hits: readonly DuplicateHit[] }) {
  const any = clusters.length > 0 || hits.length > 0;
  return (
    <Panel id="claim-fraud" title="Duplicate and fraud indicators" tone={any ? "attention" : undefined}>
      {!any ? (
        <p className="text-sm text-ink-muted">No duplicate or fraud indicators.</p>
      ) : (
        <ul className="grid gap-2 text-sm">
          {clusters.map((c) => {
            const others = c.member_claim_ids.filter((m) => m !== claimId);
            return (
              <li key={`cluster-${c.id}`} data-cluster-kind={c.kind}>
                <p className="font-medium text-warn-ink">{clusterLabel(c.kind)}</p>
                <p className="text-xs text-ink-muted">
                  {others.length} other {others.length === 1 ? "claim" : "claims"} in this group · score {c.score.toFixed(2)}
                </p>
                <ClaimLinks ids={others} />
              </li>
            );
          })}
          {hits.map((h) => (
            <li key={`hit-${h.kind}-${h.otherClaimId}`} data-hit-kind={h.kind}>
              <p className="font-medium text-warn-ink">Duplicate check: {clusterLabel(h.kind)}</p>
              {h.detail ? <p className="text-xs text-ink-muted">{h.detail}</p> : null}
              <ClaimLinks ids={[h.otherClaimId]} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function ClaimLinks({ ids }: { ids: readonly string[] }) {
  if (!ids.length) return null;
  return (
    <ul className="mt-1 flex flex-wrap gap-1">
      {ids.slice(0, 12).map((id) => (
        <li key={id}>
          <Link href={`/app/claims/${id}`} className="font-mono text-xs text-accent underline" title={id}>
            {shortId(id)}
          </Link>
        </li>
      ))}
      {ids.length > 12 ? <li className="text-xs text-ink-muted">and {ids.length - 12} more</li> : null}
    </ul>
  );
}

function change(before: Record<string, unknown> | null, after: Record<string, unknown> | null): string {
  const b = before?.status;
  const a = after?.status;
  if (typeof b === "string" && typeof a === "string") return `${b} → ${a}`;
  if (typeof a === "string") return `→ ${a}`;
  return "";
}

export function AuditTrail({ entries }: { entries: readonly AuditEntry[] }) {
  return (
    <Panel id="claim-audit" title="Audit trail">
      {entries.length === 0 ? (
        <p className="text-sm text-ink-muted">No audit entries.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Audit trail, oldest first</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-muted">
                <th scope="col" className="py-1 pr-3 font-medium">When</th>
                <th scope="col" className="py-1 pr-3 font-medium">Who</th>
                <th scope="col" className="py-1 pr-3 font-medium">Action</th>
                <th scope="col" className="py-1 font-medium">Change</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={`${e.at}-${i}`} className="border-b border-line align-top last:border-b-0">
                  <td className="py-1 pr-3 text-xs whitespace-nowrap">{formatDateTime(e.at)}</td>
                  <td className="py-1 pr-3 font-mono text-xs">{e.actor}</td>
                  <td className="py-1 pr-3">{e.action}</td>
                  <td className="py-1 text-xs">
                    {change(e.before, e.after)}
                    {e.after && Object.keys(e.after).some((k) => k !== "status") ? (
                      <details className="mt-0.5">
                        <summary className="cursor-pointer text-ink-muted">details</summary>
                        <pre className="mt-1 max-w-md overflow-x-auto whitespace-pre-wrap rounded bg-panel p-1 font-mono text-[11px]">
                          {JSON.stringify(e.after, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
