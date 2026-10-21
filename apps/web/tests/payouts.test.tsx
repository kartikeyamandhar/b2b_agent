// Phase B: payout export batches (admin): history, creating a batch, and authenticated CSV downloads.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }), usePathname: () => "/app/payouts" }));

import PayoutsPage from "@/app/app/payouts/page";
import { AppShell, CONSOLE_NAV } from "@/components/AppShell";
import { CreateExport, ExportBatchTable } from "@/components/PayoutExports";
import { ExportBatchList } from "@/lib/api-types";
import { batchAuthor, exportCsvHref, exportFilename, exportTotals } from "@/lib/payouts";

const B1 = "97532299-e8f2-49b8-8739-eb562c4378fe";
const list = ExportBatchList.parse({
  items: [
    { id: B1, created_at: "2026-10-21T05:45:13.262636Z", created_by: "seed", row_count: 56, total_cents: 279259, sha256: "5428771112a2d7bcc1486b83109d1b2684927203bf6ba683c86d1968594b9ffa" },
    { id: "0b5a6c1e-1111-4222-8333-444455556666", created_at: "2026-10-01T10:00:00Z", created_by: "user_2abc", row_count: 2, total_cents: 9900, sha256: "ab".repeat(32) },
  ],
});

type Call = { url: string; method: string };
let calls: Call[];
function serve(respond: (url: string) => Response) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET" });
    return respond(url);
  });
}

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("export helpers", () => {
  it("downloads through the authenticated proxy, never a raw id", () => {
    expect(exportCsvHref(B1)).toBe(`/api/v1/payouts/exports/${B1}.csv`);
    for (const bad of ["../internal", "x.csv", "", `${B1}/../x`]) expect(exportCsvHref(bad)).toBeNull();
  });

  it("names files by day and batch", () => {
    expect(exportFilename(list.items[0]!)).toBe("payout-export-2026-10-21-97532299.csv");
    expect(exportFilename({ id: B1, created_at: "garbage" })).toBe("payout-export-batch-97532299.csv");
  });

  it("totals the history and names the authors", () => {
    expect(exportTotals(list.items)).toEqual({ batches: 2, rows: 58, cents: 289159 });
    expect(exportTotals([])).toEqual({ batches: 0, rows: 0, cents: 0 });
    expect([batchAuthor("seed"), batchAuthor("export-job"), batchAuthor("user_2abc")]).toEqual(["Demo data", "Scheduled export job", "user_2abc"]);
  });
});

describe("components", () => {
  it("lists batches with amounts, fingerprints, and download links", () => {
    const html = renderToStaticMarkup(<ExportBatchTable items={list.items} />);
    expect(html).toContain("$2,792.59");
    expect(html).toContain(`href="/api/v1/payouts/exports/${B1}.csv"`);
    expect(html).toContain('download="payout-export-2026-10-21-97532299.csv"');
    expect(html).toContain("5428771112a2d7bc");
    expect(html.match(/data-batch=/g)).toHaveLength(4); // phone cards and the wide table
  });

  it("explains an empty history", () => {
    expect(renderToStaticMarkup(<ExportBatchTable items={[]} />)).toContain("No export batches yet");
  });

  it("asks before creating a batch (the confirmation is not shown up front)", () => {
    const html = renderToStaticMarkup(<CreateExport />);
    expect(html).toContain("data-create-export-start");
    expect(html).not.toContain("data-create-export-confirm");
  });
});

describe("/app/payouts", () => {
  it("reads the history as an admin and never creates a batch on load", async () => {
    serve(() => Response.json(list));
    const html = renderToStaticMarkup(await PayoutsPage());
    expect(calls).toEqual([{ url: "http://api.test/v1/payouts/exports?limit=100", method: "GET" }]);
    expect(html).toContain("Batch history");
    expect(html).toContain("$2,891.59");
  });

  it("refuses a reviewer without calling the API", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    serve(() => Response.json(list));
    const html = renderToStaticMarkup(await PayoutsPage());
    expect(calls).toHaveLength(0);
    expect(html).toContain("Only an admin can see and create payout export batches");
  });

  it("shows an API error with its reference", async () => {
    serve(() => Response.json({ title: "Forbidden", status: 403, detail: "admin role required", correlation_id: "c-9" }, { status: 403 }));
    const html = renderToStaticMarkup(await PayoutsPage());
    expect(html).toContain("c-9");
  });
});

describe("navigation", () => {
  it("lists Payouts for admins only", () => {
    expect(CONSOLE_NAV.find((i) => i.href === "/app/payouts")?.adminOnly).toBe(true);
    const admin = renderToStaticMarkup(<AppShell identityNote="x" role="admin">{null}</AppShell>);
    const reviewer = renderToStaticMarkup(<AppShell identityNote="x" role="reviewer">{null}</AppShell>);
    expect(admin).toContain('href="/app/payouts"');
    expect(reviewer).not.toContain('href="/app/payouts"');
    expect(admin).toContain('aria-current="page"');
  });
});
