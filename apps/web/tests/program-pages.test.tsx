// Integration: the real server pages, rendered with fetch stubbed at the API boundary.
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProgramPage from "@/app/app/programs/[id]/page";
import ProgramsPage from "@/app/app/programs/page";

const programs = JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")) as {
  items: Array<Record<string, unknown>>;
};
const DEMO = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";

type Call = { url: string; headers: Headers; method: string };
let calls: Call[];

function serve(respond: (url: string) => Response) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, headers: new Headers(init.headers), method: init.method ?? "GET" });
    return respond(url);
  });
}

const problem = (status: number, detail: string, id = "corr-1") =>
  Response.json({ type: "about:blank", title: "x", status, detail, correlation_id: id }, { status, headers: { "content-type": "application/problem+json" } });

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const render = async (page: Promise<React.ReactElement>) => renderToStaticMarkup(await page);

describe("/app/programs", () => {
  it("calls GET /v1/programs as the configured firm and renders the table", async () => {
    serve(() => Response.json(programs));
    const html = await render(ProgramsPage());
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ url: "http://api.test/v1/programs", method: "GET" });
    expect(calls[0]!.headers.get("x-dev-firm-id")).toBe("00000000-0000-4000-8000-00000000d001");
    expect(calls[0]!.headers.get("x-dev-role")).toBe("reviewer");
    expect(html).toContain("Scotch Thermal Laminators");
    expect(html).toContain(`/app/programs/${DEMO}`);
  });

  it("renders the empty state", async () => {
    serve(() => Response.json({ items: [] }));
    const html = await render(ProgramsPage());
    expect(html).toContain("No recall programs yet");
    expect(html).not.toContain("<table");
  });

  it.each([
    [401, "Your session has ended"],
    [403, "Your role does not allow"],
    [500, "Something went wrong on our side"],
    [503, "temporarily unavailable"],
  ])("renders a %i as a problem with its correlation id", async (status, sentence) => {
    serve(() => problem(status, "detail from the API", `corr-${status}`));
    const html = await render(ProgramsPage());
    expect(html).toContain('role="alert"');
    expect(html).toContain(sentence);
    expect(html).toContain("detail from the API");
    expect(html).toContain(`corr-${status}`);
  });

  it("renders an unreachable API as a 502 problem", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    const html = await render(ProgramsPage());
    expect(html).toContain("could not be reached");
    expect(html).toContain("Error 502");
  });

  it("renders a contract mismatch instead of a broken table", async () => {
    serve(() => Response.json({ items: [{ id: DEMO }] }, { headers: { "x-request-id": "req-9" } }));
    const html = await render(ProgramsPage());
    expect(html).toContain("does not match the contract");
    expect(html).toContain("req-9");
  });

  it("renders missing console auth as a configuration problem without calling the API", async () => {
    vi.stubEnv("RECALLOPS_WEB_AUTH", "");
    serve(() => Response.json(programs));
    const html = await render(ProgramsPage());
    expect(calls).toHaveLength(0);
    expect(html).toContain("console auth is not configured");
  });
});

describe("/app/programs/[id]", () => {
  it("calls GET /v1/programs/{id} and renders the overview", async () => {
    serve(() => Response.json(programs.items[0]));
    const html = await render(ProgramPage({ params: Promise.resolve({ id: DEMO }) }));
    expect(calls[0]!.url).toBe(`http://api.test/v1/programs/${DEMO}`);
    expect(html).toContain("Scotch Thermal Laminators");
    expect(html).toContain("Claims by status");
    expect(html).toContain('href="/app/programs"');
  });

  it("renders another firm's program (404) as not found", async () => {
    serve(() => problem(404, "program not found", "corr-404"));
    const html = await render(ProgramPage({ params: Promise.resolve({ id: DEMO }) }));
    expect(html).toContain("not found, or you do not have access");
    expect(html).toContain("corr-404");
  });

  it("never sends a malformed id to the API", async () => {
    serve(() => Response.json(programs.items[0]));
    const html = await render(ProgramPage({ params: Promise.resolve({ id: "../internal/feeds" }) }));
    expect(calls).toHaveLength(0);
    expect(html).toContain("not found");
  });
});
