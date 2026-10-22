// Visual review of the console's operations pages (audit lens console-b, plus the operator items): KPI tiles,
// the payouts table, settings alignment, billing, the reports frame, the MPR panel, recall access and the operator
// queue, the gated sidebar, and the console's not-found page. Markup is asserted where the defect lived in a class.
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({
  ...(await orig<typeof import("next/navigation")>()),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/app/programs",
}));

import ConsoleLayout from "@/app/app/layout";
import ConsoleNotFound from "@/app/app/not-found";
import CampaignPage from "@/app/app/programs/[id]/campaign/page";
import FraudRingsPage from "@/app/app/programs/[id]/fraud/page";
import ReportsPage from "@/app/app/programs/[id]/reports/page";
import ProgramSettingsPage from "@/app/app/programs/[id]/settings/page";
import { GATED_NAV, gateConsole, gateNav } from "@/components/AccessGate";
import { AccessRequests } from "@/components/AccessRequests";
import { AppShell, CONSOLE_NAV } from "@/components/AppShell";
import { BillingScreen } from "@/components/BillingScreen";
import { BillingView } from "@/components/BillingView";
import { CampaignStatus } from "@/components/Campaign";
import { FraudRings } from "@/components/FraudRings";
import { MprGenerate } from "@/components/MprGenerate";
import { OperatorQueue } from "@/components/OperatorQueue";
import { ExportBatchTable, ExportSummary } from "@/components/PayoutExports";
import { ProgramSettings } from "@/components/ProgramSettings";
import { CampaignSummary, ExportBatchList, FraudGraphView, Program, Usage, type AccessRequest, type AccessStatus, type OperatorAccessRequest } from "@/lib/api-types";
import { accessBadgeClass, requesterLabel } from "@/lib/access";
import { MANUAL_NUMBER_FIELDS } from "@/lib/mpr";

const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const program = Program.parse((read("programs.json") as { items: unknown[] }).items[0]);
const free = Usage.parse(read("usage-free.json"));
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const withQuery = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

const notFound = () => Response.json({ title: "Not Found", status: 404, detail: "program not found", correlation_id: "nf-1" }, { status: 404 });
function serve(handler: (url: string) => Response) {
  vi.stubGlobal("fetch", async (url: string) => handler(url));
}

beforeEach(() => {
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("KPI tiles keep words and numbers whole and their values level", () => {
  const graph = FraudGraphView.parse({
    program_id: P,
    truncated: false,
    nodes: [
      { claim_id: id(1), status: "flagged", cluster_kinds: ["same_photo"] },
      { claim_id: id(2), status: "denied", cluster_kinds: ["same_photo"] },
      { claim_id: id(3), status: "flagged", cluster_kinds: ["same_photo"] },
    ],
    edges: [
      { a: id(1), b: id(2), kind: "same_photo" },
      { a: id(2), b: id(3), kind: "same_photo" },
    ],
    rings: [[id(1), id(2), id(3)]],
  });
  const campaign = CampaignSummary.parse({ registrations: 3, matched: 2, unmatched: 1, claim_link: "https://recallops.example/intake/abc", notifications: {}, sample: [], simulated: true });
  const batches = ExportBatchList.parse({
    items: [{ id: id(9), created_at: "2026-10-21T05:45:13Z", created_by: "seed", row_count: 56, total_cents: 279259, sha256: "a".repeat(64) }],
  }).items;

  const cases: [string, string, string][] = [
    ["fraud rings", renderToStaticMarkup(<FraudRings graph={graph} />), "lg:grid-cols-4"],
    ["campaign counts", renderToStaticMarkup(<CampaignStatus summary={campaign} />), "lg:grid-cols-4"],
    ["export totals", renderToStaticMarkup(<ExportSummary items={batches} />), "lg:grid-cols-3"],
  ];

  it.each(cases)("%s: columns follow the room the sidebar leaves (lg), not the viewport's sm", (_name, html, cols) => {
    expect(html).toContain(`grid grid-cols-2 gap-3 ${cols}`);
    expect(html).not.toMatch(/sm:grid-cols-[34]/);
  });

  it.each(cases)("%s: each tile is a column with its value pinned to the bottom, never cut mid-token", (_name, html) => {
    const tiles = html.match(/<div class="flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-4[^"]*">/g) ?? [];
    expect(tiles.length).toBeGreaterThanOrEqual(3);
    expect(html).toContain('class="kpi-value mt-auto pt-2 break-words"');
    expect(html).not.toContain("overflow-wrap:anywhere]\">"); // the value no longer splits "3 claim/s" or "$2,792./59"
  });

  it("still reads the fraud values", () => {
    expect(cases[0]![1]).toContain(">3 claims<");
  });

  it("export totals: the third tile takes the full row below lg instead of sitting beside an empty cell", () => {
    expect(cases[2]![1]).toContain('px-4 py-4 col-span-2 lg:col-span-1"><dt class="eyebrow">Total exported</dt>');
  });
});

describe("the payouts table never widens the page", () => {
  const items = ExportBatchList.parse({
    items: [
      { id: id(1), created_at: "2026-10-21T05:45:13Z", created_by: "seed", row_count: 56, total_cents: 279259, sha256: "b".repeat(64) },
      { id: id(2), created_at: "2026-10-20T05:45:13Z", created_by: "user_2abcdefghijklmnopqrstuvwxyz0", row_count: 2, total_cents: 1000, sha256: "c".repeat(64) },
    ],
  }).items;
  const html = renderToStaticMarkup(<ExportBatchTable items={items} />);

  it("clips the header's sr-only label inside a positioned scroller", () => {
    expect(html).toContain('class="relative hidden overflow-x-auto rounded-lg border border-line bg-surface lg:block"');
    expect(html).toContain('class="grid grid-cols-1 gap-2 lg:hidden"'); // cards until the table fits
  });

  it("keeps the download button and the author on one line, and the fingerprint column for xl", () => {
    expect(html.match(/class="inline-block whitespace-nowrap rounded-md[^"]*"[^>]*data-download=/g)).toHaveLength(4);
    expect(html).toMatch(/<th scope="col" class="[^"]*hidden xl:table-cell">File fingerprint \(sha256\)<\/th>/);
    expect(html).toContain('<td class="hidden max-w-56 px-3 py-2 xl:table-cell">');
    expect(html).toContain('<span class="block max-w-40 truncate whitespace-nowrap" title="user_2abcdefghijklmnopqrstuvwxyz0">');
    expect(html.match(/data-batch=/g)).toHaveLength(4); // cards and table both still list every batch
  });
});

describe("rules and settings: fields stay level in their row", () => {
  it("puts every field's input at the top of its cell, so a neighbour's notice quote cannot push it down", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={program} canAdmin />);
    for (const label of ["Sold from", "Sold to", "Lowest price (USD)", "Highest price (USD)"]) {
      expect(html).toContain(`<div class="grid min-w-0 content-start gap-1" data-field="true"><label class="grid content-start gap-1 text-xs"><span class="font-medium text-ink">${label}</span>`);
    }
  });
});

describe("billing", () => {
  it("formats model spend as currency, not the API's six decimals", () => {
    expect(renderToStaticMarkup(<BillingView usage={free} />)).toContain("$0.0123");
    const zero = renderToStaticMarkup(<BillingView usage={{ ...free, model_cost_usd: "0.000000" }} />);
    expect(zero).toMatch(/data-model-spend="true">\$0\.00</);
    expect(zero).not.toContain("0.000000");
  });

  it("does not repeat the plan name as an access line, and still warns when access is limited", () => {
    const ent = renderToStaticMarkup(<BillingView usage={{ ...free, plan: "enterprise", effective_plan: "enterprise" }} />);
    expect(ent).not.toContain("Access:");
    expect(ent.match(/Enterprise/g)).toHaveLength(1);
    const limited = renderToStaticMarkup(<BillingView usage={{ ...free, plan: "growth", effective_plan: "free" }} />);
    expect(limited).toContain("data-access-limited");
  });

  it("gives the page the same header block as its sibling pages", async () => {
    serve(() => Response.json(free));
    const html = withQuery(await BillingScreen({ returned: false }));
    expect(html).toMatch(/^<div class="grid grid-cols-1 gap-6"><header><h1 class="page-title">Billing and usage<\/h1><p class="mt-2 max-w-2xl text-ink-muted">/);
  });
});

describe("product pages share one frame, also for a missing or foreign product", () => {
  const pages = {
    reports: () => ReportsPage({ params: Promise.resolve({ id: P }), searchParams: Promise.resolve({}) }),
    settings: () => ProgramSettingsPage({ params: Promise.resolve({ id: P }) }),
    fraud: () => FraudRingsPage({ params: Promise.resolve({ id: P }) }),
    campaign: () => CampaignPage({ params: Promise.resolve({ id: P }) }),
  } as const;
  const headings = { reports: "Monthly progress reports", settings: "Rules and settings", fraud: "Fraud rings", campaign: "Owner notices" } as const;

  it.each(Object.keys(pages) as (keyof typeof pages)[])("%s: breadcrumb, then heading, then the alert", async (name) => {
    serve(notFound);
    const html = renderToStaticMarkup(await pages[name]());
    const crumb = html.indexOf("← Products");
    const heading = html.indexOf(`<h1 class="page-title">${headings[name]}</h1>`);
    const alert = html.indexOf('role="alert"');
    expect(crumb).toBeGreaterThan(-1);
    expect(heading).toBeGreaterThan(crumb);
    expect(alert).toBeGreaterThan(heading);
    expect(html.startsWith('<div class="grid grid-cols-1 gap-6">')).toBe(true);
    expect(html).not.toContain("program not found"); // a 404's sentence already says it
  });

  it("reports: the same breadcrumb and description as the fraud page when the program loads", async () => {
    serve((url) => (new URL(url).pathname.endsWith("/mpr") ? Response.json({ title: "Not Found", status: 404, detail: "no report" }, { status: 404 }) : Response.json(program)));
    const html = renderToStaticMarkup(await pages.reports());
    expect(html).toContain('<nav aria-label="Breadcrumb" class="text-xs">');
    expect(html).toContain('<span class="mx-1 text-ink-muted">/</span>');
    expect(html).toContain('<p class="mt-2 max-w-2xl text-ink-muted">The CPSC monthly progress report');
    expect(html).toContain("data-mpr-generate");
  });
});

describe("the MPR boxes panel", () => {
  it("has the padding and inputs of every other card", () => {
    const html = renderToStaticMarkup(<MprGenerate programId={P} period="2026-10" canGenerate hasReport={false} />);
    expect(html).toContain('class="grid gap-4 rounded-lg border border-line bg-surface p-4 sm:p-5"');
    expect(html).not.toContain("px-2 py-1 text-sm");
    // Every number box, the homepage choice, and its explanation.
    expect(html.match(/class="w-full min-w-0 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm"/g)?.length).toBe(MANUAL_NUMBER_FIELDS.length + 2);
    expect(html).toContain('class="grid content-end gap-1 text-xs"');
  });
});

const REQ: AccessRequest = { id: "a1", recall_id: "r1", title: "3M Company Recalls Scotch Thermal Laminators Due to Risk of Serious Injury", typed: false, status: "requested", created_at: "2026-10-22T00:10:00Z", decided_at: null };
const status = (s: Partial<AccessStatus>): AccessStatus => ({ required: true, approved: false, operator: false, requests: [], ...s });
const OP: OperatorAccessRequest = {
  id: "o1",
  firm_id: "f1",
  firm_name: "Trace Demo",
  recall_id: "r1",
  recall_number: "27024",
  title: REQ.title,
  typed: false,
  typed_company: null,
  status: "requested",
  requested_by: "migration",
  requester_domain: null,
  notice_company: "3M Company",
  notice_sites: [],
  domain_matches: false,
  approved_elsewhere: null,
  decided_by: null,
  decided_at: null,
  note: null,
  created_at: "2026-10-22T00:25:00Z",
};

describe("recall access and the operator queue line up with the other console pages", () => {
  it("are left aligned at the main gutter", () => {
    const access = renderToStaticMarkup(<AccessRequests status={status({ requests: [REQ] })} />);
    const queue = renderToStaticMarkup(<OperatorQueue requests={[OP]} />);
    expect(access).toContain('<div class="grid w-full max-w-3xl gap-6" data-access-page="true">');
    expect(queue).toContain('<div class="grid w-full max-w-4xl gap-6" data-operator-queue="true">');
    expect(access).not.toContain("mx-auto");
    expect(queue).not.toContain("mx-auto");
  });
});

describe("operator queue cards", () => {
  const html = renderToStaticMarkup(<OperatorQueue requests={[OP, { ...OP, id: "o2", status: "approved", requested_by: "user_2xyz" }]} />);

  it("keep the status top right as a pill beside the title from sm, and under it on a phone", () => {
    expect(html).not.toContain("flex flex-wrap items-start justify-between");
    expect(html).toContain('<div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3"><div class="min-w-0 flex-1">');
    expect(html).toContain(`<span class="${accessBadgeClass("requested")}" data-access-status="requested">Waiting for review</span>`);
    expect(html).toContain(`<span class="${accessBadgeClass("approved")}" data-access-status="approved">Approved</span>`);
    expect(accessBadgeClass("requested")).toContain("shrink-0");
    expect(accessBadgeClass("requested")).toContain("whitespace-nowrap");
  });

  it("say what made a migration request, and give the date its own row", () => {
    expect(html).not.toMatch(/>migration[,<]/);
    expect(html).toContain("Created automatically for an existing program");
    expect(html).toContain(">user_2xyz<");
    expect(html).toContain(">Requested on</dt><dd>Oct 22, 2026, 00:25 UTC</dd>");
    expect([requesterLabel("migration"), requesterLabel("user_1"), requesterLabel("")]).toEqual(["Created automatically for an existing program", "user_1", "Not recorded"]);
  });
});

describe("recall access at phone width", () => {
  const html = renderToStaticMarkup(<AccessRequests status={status({ requests: [REQ] })} />);

  it("keeps each source option on one line and the search box uncut", () => {
    expect(html).toMatch(/role="radio" aria-checked="true" class="whitespace-nowrap[^"]*">CPSC archive</);
    expect(html).toMatch(/role="radio" aria-checked="false" class="whitespace-nowrap[^"]*">Typed notice</);
    expect(html).toContain('<form class="flex flex-col gap-2 sm:flex-row">');
    expect(html).toContain('placeholder="Company, product, or recall number"');
  });

  it("shows a request's status as the same pill, beside the title from sm and under it on a phone", () => {
    expect(html).toContain('<div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3"><p class="min-w-0 flex-1 break-words font-medium text-ink">');
    expect(html).toContain(`<span class="self-start ${accessBadgeClass("requested")}" data-access-status="requested">`);
  });

  it("uses the same pill for an already-requested recall in the search results", () => {
    // Search results live in client state, which static rendering never fills; the source is the check.
    const src = readFileSync(new URL("../src/components/AccessRequests.tsx", import.meta.url), "utf8");
    expect(src).toContain("accessBadgeClass(existing.status)");
    expect(src).not.toContain('<span className="text-sm text-ink-muted">{accessStatusLabel(existing.status)}</span>');
  });
});

describe("the gated console offers only Recall access and Billing", () => {
  const child = <p data-console-child>queue</p>;
  const as = (s: AccessStatus) => async () => ({ ok: true as const, data: s });
  // GET /v1/me for an onboarded company (plan v3): the layout routes on it before the approval gate.
  const me = (approved: boolean) => ({
    operator: false,
    email: "owner@example.com",
    firm: { id: "00000000-0000-4000-8000-00000000d001", name: "Acme", onboarded: true, plan: "free", effective_plan: "free", subscription_status: null, approved, active_products: approved ? 1 : 0, product_limit: 1 },
  });
  const layoutFor = (approved: boolean) =>
    serve((url) => {
      const path = new URL(url).pathname;
      if (path === "/v1/me") return Response.json(me(approved));
      if (path === "/v1/access") return Response.json(status(approved ? { approved: true } : { requests: [REQ] }));
      return notFound();
    });

  it("marks the gate and narrows the sidebar while the company is not approved", async () => {
    const gated = await gateConsole(child, as(status({ requests: [REQ] })));
    expect(gated.gated).toBe(true);
    expect(gateNav(gated)).toBe(GATED_NAV);
    expect(GATED_NAV.map((i) => i.href)).toEqual(["/app/billing", "/app/access"]);
    const html = renderToStaticMarkup(<AppShell identityNote="n" role="admin" items={gateNav(gated)}>{gated.body}</AppShell>);
    expect(html).toContain(">Recall access<");
    expect(html).toContain(">Billing<");
    for (const label of ["Products", "Payouts", "Operator queue"]) expect(html).not.toContain(`>${label}<`);
  });

  it("keeps the whole console for an approved company and an unreadable status", async () => {
    for (const s of [status({ approved: true }), status({ required: false })]) {
      const gate = await gateConsole(child, as(s));
      expect(gate.gated).toBe(false);
      expect(gateNav(gate)).toBe(CONSOLE_NAV);
    }
    const down = await gateConsole(child, async () => ({ ok: false as const, problem: { status: 503, title: "Down", detail: null, correlationId: null, retryAfterSeconds: null } }));
    expect(down.gated).toBe(false);
    expect(gateNav(null)).toBe(CONSOLE_NAV);
  });

  it("dev-mode layout: an unapproved company's sidebar has only Billing and Recall access", async () => {
    layoutFor(false);
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).not.toContain("data-console-child");
    expect(html).toContain(">Recall access<");
    expect(html).toContain(">Billing<");
    for (const label of ["Products", "Payouts"]) expect(html).not.toContain(`>${label}<`);
  });

  it("dev-mode layout: an approved company keeps Products, Payouts, and Billing", async () => {
    layoutFor(true);
    const html = renderToStaticMarkup(await ConsoleLayout({ children: child }));
    expect(html).toContain("data-console-child");
    for (const label of ["Products", "Payouts", "Billing", "Recall access"]) expect(html).toContain(`>${label}<`);
  });
});

describe("the console's not-found page", () => {
  it("is a left-aligned console page with a way back", () => {
    const html = renderToStaticMarkup(<ConsoleNotFound />);
    expect(html).toContain('<h1 class="page-title">Page not found</h1>');
    expect(html).toContain('href="/app/programs"');
    expect(html).toContain('href="/"');
    expect(html).not.toContain("mx-auto");
    expect(html).not.toContain("404");
  });
});
