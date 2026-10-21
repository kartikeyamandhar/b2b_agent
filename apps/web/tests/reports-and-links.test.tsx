import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ReportsPage from "@/app/app/programs/[id]/reports/page";
import { IssueLinks, linksCsv, requestLinks } from "@/components/IssueLinks";
import { MprGenerate, manualBody } from "@/components/MprGenerate";
import { MprReportView } from "@/components/MprReportView";
import { MprReport } from "@/lib/api-types";
import { boxLabel, boxSource, boxValue, isPeriod, periodLabel, reportPeriods } from "@/lib/mpr";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const report = MprReport.parse(read("mpr-report.json"));
const program = (read("programs.json") as { items: unknown[] }).items[0];
const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("MPR rules", () => {
  it("lists months from the program start to now, newest first", () => {
    expect(reportPeriods("2026-08-14T00:00:00Z", new Date("2026-10-21T00:00:00Z"))).toEqual(["2026-10", "2026-09", "2026-08"]);
    expect(reportPeriods("2025-12-31T23:00:00Z", new Date("2026-01-05T00:00:00Z"))).toEqual(["2026-01", "2025-12"]);
    expect(reportPeriods("not a date", new Date("2026-10-21T00:00:00Z"))).toEqual(["2026-10"]);
    expect(reportPeriods("2020-01-01T00:00:00Z", new Date("2026-10-21T00:00:00Z"))).toHaveLength(24);
  });

  it("accepts only real months", () => {
    expect(isPeriod("2026-10")).toBe(true);
    for (const p of ["2026-13", "2026-1", "2026-00", "", undefined, "2026-10-01"]) expect(isPeriod(p)).toBe(false);
    expect(periodLabel("2026-10")).toBe("October 2026");
  });

  it("labels, formats, and sources boxes", () => {
    expect(boxLabel("cumulative_corrections")).toBe("Cumulative corrections");
    expect(boxLabel("brand_new_box")).toBe("brand new box");
    expect(boxValue(53634)).toBe("53,634");
    expect(boxValue(true)).toBe("Yes");
    expect(boxValue(null)).toBe("—");
    expect(boxValue({ posts: 0, reach: 1200 })).toBe("posts 0 · reach 1,200");
    expect(boxValue({})).toBe("—");
    const p = { computed: { a: 1 }, manual: { b: 2 } };
    expect([boxSource("a", p), boxSource("b", p), boxSource("c", p)]).toEqual(["computed", "manual", "default"]);
  });

  it("sends only the boxes the firm entered, as numbers", () => {
    const blank = Object.fromEntries(["units_with_manufacturer", "units_with_distributor", "units_with_retailers", "incidents_before", "incidents_after", "injuries_before", "injuries_after", "deaths_before", "deaths_after", "notified_phone_text", "notified_email", "notified_mail", "contacts_phone", "contacts_email", "contacts_mail", "contacts_registration_card", "website_hits"].map((k) => [k, ""]));
    const m = { ...blank, homepage_posting: "", homepage_posting_explanation: "" } as never;
    expect(manualBody(m)).toEqual({ ok: true, body: {} });
    expect(manualBody({ ...(m as object), incidents_before: " 3 ", homepage_posting: "yes", homepage_posting_explanation: "banner" } as never)).toEqual({ ok: true, body: { incidents_before: 3, homepage_posting: true, homepage_posting_explanation: "banner" } });
    expect(manualBody({ ...(m as object), deaths_after: "-1" } as never)).toMatchObject({ ok: false });
    expect(manualBody({ ...(m as object), website_hits: "1.5" } as never)).toMatchObject({ ok: false });
  });
});

describe("MprReportView", () => {
  const out = renderToStaticMarkup(<MprReportView programId={P} report={report} />);

  it("shows the reconciliation result, the PDF, and every box in form order", () => {
    expect(out).toContain('data-reconciled="true"');
    expect(out).toContain("Reconciled: the independent recount agrees");
    expect(out).toContain(`href="/api/v1/programs/${P}/mpr/${report.id}.pdf"`);
    expect(out).toContain('download="mpr-2026-10.pdf"');
    const order = [...out.matchAll(/data-box="([a-z_]+)"/g)].map((m) => m[1]);
    expect(order.slice(0, 3)).toEqual(["total_units_affected", "corrections_this_period", "cumulative_corrections"]);
    expect(order).toContain("brand_new_box");
    const units = out.split("<tr").find((r) => r.includes('data-box="total_units_affected"'))!;
    expect(units).toContain("whitespace-nowrap");
  });

  it("marks hand-entered boxes and computed ones", () => {
    const row = out.split("<tr").find((r) => r.includes('data-box="incidents_before"'))!;
    expect(row).toContain("entered by hand");
    expect(out.split("<tr").find((r) => r.includes('data-box="cumulative_corrections"'))).toContain("from claims");
  });

  it("restores CPSC form order when the stored payload comes back in JSONB key order", () => {
    const shuffled = { ...report, payload: { ...report.payload, boxes: { deaths_after: 0, social_posts: {}, brand_new_box: "x", total_units_affected: 53634, cumulative_corrections: 110 } } };
    const html = renderToStaticMarkup(<MprReportView programId={P} report={shuffled} />);
    expect([...html.matchAll(/data-box="([a-z_]+)"/g)].map((m) => m[1])).toEqual(["total_units_affected", "cumulative_corrections", "deaths_after", "social_posts", "brand_new_box"]);
  });

  it("flags discrepancies and an unexpected payload", () => {
    const bad = renderToStaticMarkup(<MprReportView programId={P} report={{ ...report, reconciled: false, payload: { ...report.payload, reconciliation: { discrepancies: [{ box: "cumulative_corrections", report: 110, recount: 109 }] } } }} />);
    expect(bad).toContain("Not reconciled: 1 discrepancy");
    expect(bad).toContain("Recount discrepancies");
    const odd = renderToStaticMarkup(<MprReportView programId={P} report={{ ...report, payload: { boxes: "nope" } }} />);
    expect(odd).toContain("did not have the expected shape");
  });
});

describe("MprGenerate", () => {
  it("is for admins only", () => {
    expect(renderToStaticMarkup(<MprGenerate programId={P} period="2026-10" canGenerate={false} hasReport={false} />)).toContain("Only an admin");
    const admin = renderToStaticMarkup(<MprGenerate programId={P} period="2026-10" canGenerate hasReport />);
    expect(admin).toContain("Regenerate the October 2026 report");
    expect(admin).toContain("Boxes the firm fills in");
  });
});

describe("/app/programs/[id]/reports", () => {
  let urls: string[];
  beforeEach(() => {
    urls = [];
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  });
  const serve = (mpr: () => Response) =>
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new URL(url).pathname.endsWith("/mpr") ? mpr() : Response.json(program);
    });
  const page = async (search: Record<string, string> = {}) => renderToStaticMarkup(await ReportsPage({ params: Promise.resolve({ id: P }), searchParams: Promise.resolve(search) }));

  it("loads the requested period's report", async () => {
    serve(() => Response.json(report));
    const out = await page({ period: "2026-10" });
    expect(urls).toContain(`http://127.0.0.1:8000/v1/programs/${P}/mpr?period=2026-10`);
    expect(out).toContain("Report for October 2026");
    expect(out).toContain('aria-current="page"');
  });

  it("treats 'no report for that period' as an empty state, and admins can generate", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    serve(() => Response.json({ title: "Not Found", status: 404, detail: "no report for that period; generate one first" }, { status: 404 }));
    const out = await page({ period: "2026-10" });
    expect(out).toContain("No report for October 2026 yet");
    expect(out).toContain("data-mpr-generate");
  });

  it("ignores a malformed period and hides generation from reviewers", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    serve(() => Response.json(report));
    const out = await page({ period: "2026-13" });
    expect(urls.some((u) => u.includes("period=2026-13"))).toBe(false);
    expect(out).toContain("Only an admin can generate reports.");
  });
});

describe("Issue claim links", () => {
  it("posts the count and returns the links (mocked API)", async () => {
    const seen: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen.push({ url, body: JSON.parse(String(init.body)) });
      return Response.json({ links: [{ claim_id: "c1", url: "http://localhost:3000/claim/tok1" }] });
    });
    expect(await requestLinks(P, 3)).toEqual([{ claim_id: "c1", url: "http://localhost:3000/claim/tok1" }]);
    expect(seen[0]).toEqual({ url: `/api/v1/programs/${P}/claims/issue`, body: { n: 3 } });
    await expect(requestLinks(P, 0)).rejects.toThrow();
    await expect(requestLinks(P, 10001)).rejects.toThrow();
  });

  it("writes a CSV with quoted fields", () => {
    expect(linksCsv([{ claim_id: "c1", url: 'http://x/claim/a"b' }])).toBe('claim_id,url\n"c1","http://x/claim/a""b"\n');
  });

  it("is offered to admins on open programs only", () => {
    expect(renderToStaticMarkup(<IssueLinks programId={P} open canIssue={false} />)).toContain("Only an admin can issue claim links.");
    expect(renderToStaticMarkup(<IssueLinks programId={P} open={false} canIssue />)).toContain("Open the program before issuing");
    expect(renderToStaticMarkup(<IssueLinks programId={P} open canIssue />)).toContain("data-issue-links");
  });
});
