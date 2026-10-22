"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { IssueRequest, IssueResponse, type IssuedLink } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { shortId } from "@/lib/claims";
import { countOf, formatCount } from "@/lib/format";
import { qrSheetRanges } from "@/lib/qr";

import { ProblemAlert } from "./ProblemAlert";
import { QrPanel, openQrSheet, saveBlob } from "./QrCode";

/** CSV the firm distributes through its own channels (04-login.md: links are exported for distribution). */
export function linksCsv(links: readonly IssuedLink[]): string {
  const q = (v: string) => `"${v.replace(/"/g, '""')}"`;
  return ["claim_id,url", ...links.map((l) => `${q(l.claim_id)},${q(l.url)}`)].join("\n") + "\n";
}

export async function requestLinks(programId: string, n: number): Promise<IssuedLink[]> {
  const body = IssueRequest.parse({ n });
  return (await sendJson(IssueResponse, "POST", `/v1/programs/${programId}/claims/issue`, body)).links;
}

/**
 * Issue single-claim portal links (POST /v1/programs/{id}/claims/issue, admin). Each link is a bearer secret for one
 * claim: it is shown once, here, and never stored by the console.
 */
export function IssueLinks({
  programId,
  open,
  canIssue,
  closed = false,
  programTitle = "",
}: {
  programId: string;
  open: boolean;
  canIssue: boolean;
  closed?: boolean;
  /** Heads the printed sheet of QR codes. */
  programTitle?: string;
}) {
  const router = useRouter();
  const [n, setN] = useState("10");
  const [links, setLinks] = useState<IssuedLink[]>([]);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!canIssue) {
    return <p className="text-sm text-ink-muted">Only an admin can issue claim links.</p>;
  }
  if (closed) {
    return <p className="text-sm text-ink-muted">The program is closed: its claim links no longer work.</p>;
  }
  if (!open) {
    return <p className="text-sm text-ink-muted">Open the program before issuing claim links.</p>;
  }

  async function issue() {
    const body = IssueRequest.safeParse({ n: Number(n) });
    if (!body.success || !Number.isInteger(Number(n)) || Number(n) < 1 || Number(n) > 10000) {
      setError("Enter a whole number from 1 to 10,000.");
      return;
    }
    setBusy(true);
    setError(null);
    setProblem(null);
    try {
      setLinks(await requestLinks(programId, body.data.n));
      router.refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.problem : { status: 0, title: "Error", detail: null, correlationId: null, retryAfterSeconds: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void issue();
        }}
      >
        <label className="grid gap-1 text-xs">
          Number of links
          <input
            type="number"
            min={1}
            max={10000}
            value={n}
            onChange={(e) => setN(e.target.value)}
            aria-invalid={error ? true : undefined}
            className="w-28 rounded border border-line-strong bg-surface px-2 py-1 text-sm"
          />
        </label>
        <button type="submit" disabled={busy} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-60 hover:opacity-90" data-issue-links>
          {busy ? "Issuing…" : "Issue claim links"}
        </button>
      </form>
      {error ? (
        <p role="alert" className="text-xs text-warn-ink">
          {error}
        </p>
      ) : null}
      {problem ? <ProblemAlert problem={problem} /> : null}
      {links.length ? <IssuedLinkList links={links} programTitle={programTitle} programId={programId} /> : null}
    </div>
  );
}

/** The links just issued: copy one, download them all as CSV, open one's QR code, or print every code on sheets of
 *  QR_SHEET_MAX. Everything stays in this component's memory, like the links themselves. */
export function IssuedLinkList({ links, programTitle, programId }: { links: readonly IssuedLink[]; programTitle: string; programId: string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const [qrFor, setQrFor] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const ranges = qrSheetRanges(links.length);

  function download() {
    saveBlob(new Blob([linksCsv(links)], { type: "text/csv" }), `claim-links-${programId.slice(0, 8)}.csv`);
  }

  async function copy(link: IssuedLink) {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(link.claim_id);
    } catch {
      setCopied(null);
    }
  }

  function print(first: number, last: number) {
    const items = links.slice(first - 1, last).map((l) => ({ label: `Claim ${shortId(l.claim_id)}`, url: l.url }));
    const heading = programTitle ? `Claim QR codes: ${programTitle}` : "Claim QR codes";
    setBlocked(!openQrSheet(items, ranges.length > 1 ? `${heading} (${formatCount(first)} to ${formatCount(last)})` : heading));
  }

  return (
    <div className="grid gap-2" data-issued-links>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{countOf(links.length, "link")} issued</p>
        <button type="button" onClick={download} className="rounded-lg border border-line bg-surface px-2 py-1 text-xs">
          Download CSV
        </button>
        {ranges.map(([first, last]) => (
          <button key={first} type="button" onClick={() => print(first, last)} className="rounded-lg border border-line bg-surface px-2 py-1 text-xs" data-print-qr>
            {ranges.length > 1 ? `Print QR codes ${formatCount(first)} to ${formatCount(last)}` : "Print QR codes"}
          </button>
        ))}
      </div>
      {blocked ? (
        <p role="alert" className="text-xs text-warn-ink">
          The sheet opens in a new window: allow pop-ups for this site, then print again.
        </p>
      ) : null}
      <p className="text-xs text-warn-ink">
        Each link, and its QR code, opens one claim and works like a password. They are shown only now: download, print, or copy them
        before leaving this page. A lost link can be reissued from the claim later.
      </p>
      <ul className="max-h-96 overflow-auto rounded-lg border border-line bg-surface text-xs">
        {links.map((l) => (
          <li key={l.claim_id} className="border-b border-line px-2 py-1 last:border-b-0">
            <div className="flex items-center gap-2">
              <span className="font-mono">{shortId(l.claim_id)}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-ink-muted" title={l.url}>
                {l.url}
              </span>
              <button type="button" onClick={() => void copy(l)} className="rounded border border-line bg-surface px-1.5 py-0.5">
                {copied === l.claim_id ? "Copied" : "Copy"}
              </button>
              <button
                type="button"
                onClick={() => setQrFor(qrFor === l.claim_id ? null : l.claim_id)}
                aria-expanded={qrFor === l.claim_id}
                aria-controls={`qr-${l.claim_id}`}
                className="rounded border border-line bg-surface px-1.5 py-0.5"
                data-show-qr={l.claim_id}
              >
                QR
              </button>
            </div>
            {qrFor === l.claim_id ? (
              <div id={`qr-${l.claim_id}`} className="py-2">
                <QrPanel url={l.url} claimId={l.claim_id} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
