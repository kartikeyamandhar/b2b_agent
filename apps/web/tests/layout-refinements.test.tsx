import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), usePathname: () => "/app/claims/abc", useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ProgramsPage from "@/app/app/programs/page";
import { AppShell, CONSOLE_NAV } from "@/components/AppShell";
import { CARD_LIMIT, ProgramCards, portfolio } from "@/components/ProgramPortfolio";
import { ProgramList, type Program } from "@/lib/api-types";

const list = ProgramList.parse(JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")));
const [open, draft] = list.items as [Program, Program];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("portfolio totals", () => {
  it("adds up programs from contract fields only", () => {
    expect(portfolio(list.items)).toEqual({ open: 1, total: 2, units: 53634, filed: 500, awaitingReview: 117, corrected: 110, correctionRate: 110 / 53634 });
  });

  it("has no unit total or rate when no program states units", () => {
    expect(portfolio([draft])).toMatchObject({ units: null, correctionRate: null, filed: 0, open: 0, total: 1 });
  });
});

describe("program cards", () => {
  const html = renderToStaticMarkup(<ProgramCards programs={[open]} />);

  it("shows status, opening date, remedy, models, figures, and the next steps", () => {
    expect(html).toContain('data-status="open"');
    expect(html).toContain("Opened Oct 16, 2026");
    expect(html).toContain("Refund · Models TL1302Z, TL901X");
    expect(html).toContain(">117<");
    expect(html).toContain(`href="/app/programs/${open.id}/claims"`);
    expect(html).toContain(`href="/app/programs/${open.id}/reports"`);
  });
});

describe("/app/programs layout", () => {
  const serve = (items: unknown[]) => {
    vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
    vi.stubGlobal("fetch", async () => Response.json({ items }));
  };

  it("uses the summary and cards for a few programs", async () => {
    serve(list.items);
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).toContain('data-portfolio-stat="Open programs"');
    expect(html).toContain("data-program-card=");
    expect(html).not.toContain("<table");
  });

  it("switches to the table above the card limit", async () => {
    serve(Array.from({ length: CARD_LIMIT + 1 }, (_, i) => ({ ...open, id: `${open.id.slice(0, -1)}${i}` })));
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).toContain("<table");
    expect(html).not.toContain("data-program-card=");
  });
});

describe("sidebar", () => {
  it("groups navigation and marks Programs on a claim page", () => {
    expect([...new Set(CONSOLE_NAV.map((n) => n.group))]).toEqual(["Operations", "Account"]);
    const html = renderToStaticMarkup(<AppShell identityNote="note" org={{ name: "Development firm", detail: "…00d001 · Admin · dev auth" }}>x</AppShell>);
    expect(html).toContain(">Operations<");
    expect(html).toContain(">Account<");
    expect(html).toMatch(/aria-current="page"[^>]*href="\/app\/programs"|href="\/app\/programs"[^>]*aria-current="page"/);
  });

  it("shows the organization with initials, or the identity note without one", () => {
    const withOrg = renderToStaticMarkup(<AppShell identityNote="note" org={{ name: "Development firm", detail: "…00d001 · Admin · dev auth" }}>x</AppShell>);
    expect(withOrg).toContain("data-org-block");
    expect(withOrg).toContain(">DF<");
    expect(withOrg).toContain("Development firm");
    const without = renderToStaticMarkup(<AppShell identityNote="Console auth not configured">x</AppShell>);
    expect(without).toContain("Console auth not configured");
  });
});
