"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { IssueRequest, IssueResponse, type IssuedLink } from "@/lib/api-types";
import { sendJson } from "@/lib/api/client";
import { ApiError, type ApiProblem } from "@/lib/api/problem";
import { shortId } from "@/lib/claims";

import { ProblemAlert } from "./ProblemAlert";

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
export function IssueLinks({ programId, open, canIssue }: { programId: string; open: boolean; canIssue: boolean }) {
  const router = useRouter();
  const [n, setN] = useState("10");
  const [links, setLinks] = useState<IssuedLink[]>([]);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  if (!canIssue) {
    return <p className="text-sm text-ink-muted">Only an admin can issue claim links.</p>;
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

  function download() {
    const url = URL.createObjectURL(new Blob([linksCsv(links)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `claim-links-${programId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function copy(link: IssuedLink) {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(link.claim_id);
    } catch {
      setCopied(null);
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
      {links.length ? (
        <div className="grid gap-2" data-issued-links>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium">{links.length} links issued</p>
            <button type="button" onClick={download} className="rounded-lg border border-line bg-surface px-2 py-1 text-xs">
              Download CSV
            </button>
          </div>
          <p className="text-xs text-warn-ink">
            Each link opens one claim and works like a password. They are shown only now: download or copy them before
            leaving this page. A lost link can be reissued from the claim later.
          </p>
          <ul className="max-h-64 overflow-auto rounded-lg border border-line bg-surface text-xs">
            {links.map((l) => (
              <li key={l.claim_id} className="flex items-center gap-2 border-b border-line px-2 py-1 last:border-b-0">
                <span className="font-mono">{shortId(l.claim_id)}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-ink-muted" title={l.url}>
                  {l.url}
                </span>
                <button type="button" onClick={() => void copy(l)} className="rounded border border-line bg-surface px-1.5 py-0.5">
                  {copied === l.claim_id ? "Copied" : "Copy"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
