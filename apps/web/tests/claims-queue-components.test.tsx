import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ClaimsQueue } from "@/components/ClaimsQueue";
import { ClaimsTable } from "@/components/ClaimsTable";
import { QueuePager, ReviewSummary, StatusFilter } from "@/components/QueueControls";
import { ClaimPage, Program, ProgramList } from "@/lib/api-types";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const page = ClaimPage.parse(read("claims-page.json"));
const program = ProgramList.parse(read("programs.json")).items[0] as Program;
const P = program.id;

describe("ClaimsTable", () => {
  const html = renderToStaticMarkup(<ClaimsTable claims={page.items} />);

  it("links every claim to its detail page", () => {
    for (const c of page.items) expect(html).toContain(`href="/app/claims/${c.id}"`);
    expect(html).toContain(">66dcc492<");
  });

  it("marks rows that need a reviewer, in text and not only by color", () => {
    const rows = html.split("<tr").slice(2);
    const flagged = rows.find((r) => r.includes('data-review="needs_reviewer"'))!;
    expect(flagged).toContain("Needs a reviewer");
    expect(flagged).toContain("border-l-warn");
    expect(html.match(/data-review="needs_reviewer"/g)).toHaveLength(1);
    expect(html).toContain("Waiting on claimant");
    expect(html).toContain("Being checked");
    expect(html).toContain("Decided by a person");
  });

  it("shows status, outcome, refund and UTC times", () => {
    expect(html).toContain('data-status="flagged"');
    expect(html).toContain("$56.00");
    expect(html).toContain("Oct 16, 2026, 22:40 UTC");
  });

  it("renders unknown statuses and outcomes as in progress", () => {
    const row = html.split("<tr").find((r) => r.includes('data-claim-id="ad4b3f5a'))!;
    expect(row).toContain('data-status="archived"');
    expect(row).toContain('data-status="escalate"');
    expect(row.match(/In progress/g)).toHaveLength(2);
  });

  it("shows a dash for missing values and escapes claimant text", () => {
    expect(html).toContain("—");
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });
});

describe("StatusFilter", () => {
  const counts = { flagged: 117, approved: 72, paid: 110, draft: 3 };

  it("links each status to page one of that filter, with counts", () => {
    const html = renderToStaticMarkup(<StatusFilter programId={P} active="flagged" counts={counts} />);
    expect(html).toContain(`href="/app/programs/${P}/claims?status=flagged"`);
    expect(html).toContain(`href="/app/programs/${P}/claims"`);
    expect(html).not.toContain("cursor=");
    expect(html).toMatch(/data-filter="all"[^>]*>All<span[^>]*>302</);
    expect(html).toMatch(/data-filter="capped"[^>]*>Capped<span[^>]*>0</);
  });

  it("marks only the active chip as current", () => {
    const html = renderToStaticMarkup(<StatusFilter programId={P} active="flagged" counts={counts} />);
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    expect(html).toMatch(/aria-current="page"[^>]*data-filter="flagged"/);
    expect(renderToStaticMarkup(<StatusFilter programId={P} active={null} counts={null} />)).toMatch(/aria-current="page"[^>]*data-filter="all"/);
  });

  it("puts the reviewer statuses first", () => {
    const html = renderToStaticMarkup(<StatusFilter programId={P} active={null} counts={null} />);
    const order = [...html.matchAll(/data-filter="([a-z_]+)"/g)].map((m) => m[1]);
    expect(order.slice(0, 4)).toEqual(["all", "flagged", "under_review", "capped"]);
  });
});

describe("ReviewSummary", () => {
  it("totals the reviewer statuses and links to each", () => {
    const html = renderToStaticMarkup(<ReviewSummary programId={P} counts={{ flagged: 117, under_review: 2, capped: 1, denied: 9 }} />);
    expect(html).toContain("120 claims need a reviewer");
    expect(html).toContain(`?status=under_review`);
    expect(html).toContain("Capped: 1");
  });

  it("says when nothing is waiting", () => {
    expect(renderToStaticMarkup(<ReviewSummary programId={P} counts={{ approved: 3 }} />)).toContain("No claims are waiting");
  });
});

describe("QueuePager", () => {
  it("offers the next page with the same filter, and the first page once paged", () => {
    const html = renderToStaticMarkup(<QueuePager programId={P} params={{ status: "denied", cursor: "abc" }} nextCursor="def" shown={50} />);
    expect(html).toContain(`href="/app/programs/${P}/claims?status=denied&amp;cursor=def"`);
    expect(html).toContain(`href="/app/programs/${P}/claims?status=denied"`);
    expect(html).toContain("50 claims on this page");
  });

  it("shows no links on a single page", () => {
    const html = renderToStaticMarkup(<QueuePager programId={P} params={{ status: null, cursor: null }} nextCursor={null} shown={1} />);
    expect(html).not.toContain("<a");
    expect(html).toContain("1 claim on this page");
  });
});

describe("ClaimsQueue (server render)", () => {
  const render = (el: ReactElement) =>
    renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);
  const base = { programId: P, params: { status: null, cursor: null }, initialProgram: program, loadedAt: Date.UTC(2026, 9, 20, 12, 0, 5) };

  it("renders the server's first page without waiting for the browser", () => {
    const html = render(<ClaimsQueue {...base} initialPage={{ ok: true, data: page }} />);
    expect(html).toContain("<table");
    expect(html).toContain("Updated 12:00:05 UTC");
    expect(html).toContain("refreshes every 10 s while this tab is visible");
    expect(html).toContain('data-page="next"');
    expect(html).toContain("117 claims need a reviewer");
  });

  it("renders the server's problem when the first load failed", () => {
    const problem = { status: 400, title: "Bad Request", detail: "invalid cursor", correlationId: "c-400", retryAfterSeconds: null };
    const html = render(<ClaimsQueue {...base} initialPage={{ ok: false, problem }} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("invalid cursor");
    expect(html).toContain("c-400");
    expect(html).toContain("Try again");
    expect(html).not.toContain("<table");
  });

  it("renders a filtered empty state", () => {
    const html = render(<ClaimsQueue {...base} params={{ status: "capped", cursor: null }} initialPage={{ ok: true, data: { items: [], next_cursor: null } }} />);
    expect(html).toContain("No claims with status Capped");
    expect(html).not.toContain("<table");
  });
});
