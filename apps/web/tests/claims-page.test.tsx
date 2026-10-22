// Integration: the real server page with fetch stubbed at the API boundary.
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ClaimsQueuePage from "@/app/app/programs/[id]/claims/page";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const program = (read("programs.json") as { items: unknown[] }).items[0];
const page = read("claims-page.json");
const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";

let calls: string[];
type Route = (url: URL) => Response | undefined;

function serve(route: Route = () => undefined) {
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const u = new URL(url);
    const custom = route(u);
    if (custom) return custom;
    if (u.pathname === `/v1/programs/${P}`) return Response.json(program);
    if (u.pathname === `/v1/programs/${P}/claims`) return Response.json(page);
    return Response.json({ title: "Not Found", status: 404, detail: "program not found", correlation_id: "c-404" }, { status: 404 });
  });
}

async function render(id: string, search: Record<string, string> = {}) {
  const el = await ClaimsQueuePage({ params: Promise.resolve({ id }), searchParams: Promise.resolve(search) });
  return renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);
}

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/app/programs/[id]/claims", () => {
  it("loads the program and the first page of claims", async () => {
    serve();
    const html = await render(P);
    expect(calls.sort()).toEqual([`http://127.0.0.1:8000/v1/programs/${P}`, `http://127.0.0.1:8000/v1/programs/${P}/claims?limit=50`]);
    expect(html).toContain("Claims queue");
    expect(html).toContain("Scotch Thermal Laminators");
    expect(html).toContain('href="/app/claims/66dcc492-086f-5b59-b441-cbe001eb2e0b"');
  });

  it("sends the status filter and cursor to the API", async () => {
    serve();
    await render(P, { status: "flagged", cursor: "YWJj" });
    expect(calls).toContain(`http://127.0.0.1:8000/v1/programs/${P}/claims?status=flagged&cursor=YWJj&limit=50`);
  });

  it("drops an unknown status and a malformed cursor before calling, and says so", async () => {
    serve();
    const html = await render(P, { status: "archived", cursor: "not a cursor" });
    expect(calls).toContain(`http://127.0.0.1:8000/v1/programs/${P}/claims?limit=50`);
    expect(html).toContain("&quot;archived&quot; is not a claim status");
    expect(html).toContain("The page link was not valid");
  });

  it("shows the API's 400 for a stale cursor with its correlation id", async () => {
    serve((u) =>
      u.pathname.endsWith("/claims")
        ? Response.json({ title: "Bad Request", status: 400, detail: "invalid cursor", correlation_id: "c-400" }, { status: 400 })
        : undefined,
    );
    const html = await render(P, { cursor: "c3RhbGU" });
    expect(html).toContain("Invalid cursor.");
    expect(html).toContain("c-400");
    expect(html).not.toContain("<table");
  });

  it("shows another firm's program as not found", async () => {
    serve(() => Response.json({ title: "Not Found", status: 404, detail: "program not found", correlation_id: "c-404" }, { status: 404 }));
    const html = await render("11111111-1111-4111-8111-111111111111");
    expect(html).toContain("not found, or you do not have access");
    expect(html).not.toContain("<table");
  });

  it("never sends a malformed program id", async () => {
    serve();
    const html = await render("../../internal");
    expect(calls).toHaveLength(0);
    expect(html).toContain("not found");
  });

  it("rejects a page that does not match the contract", async () => {
    serve((u) => (u.pathname.endsWith("/claims") ? Response.json({ items: [{ id: "x", status: "flagged" }], next_cursor: null }) : undefined));
    const html = await render(P);
    expect(html).toContain("does not match the contract");
  });
});
