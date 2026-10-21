import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ClaimPage from "@/app/app/claims/[id]/page";
import TracePage from "@/app/app/claims/[id]/trace/page";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const claim = read("claim-detail.json") as Record<string, unknown>;
const trace = read("trace-agent.json");
const C = "66dcc492-086f-5b59-b441-cbe001eb2e0b";
let calls: string[];

const problem = (status: number, detail: string) => Response.json({ title: "x", status, detail, correlation_id: `c-${status}` }, { status });

function serve(over: { claim?: () => Response; trace?: () => Response } = {}) {
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const p = new URL(url).pathname;
    if (p === `/v1/claims/${C}`) return over.claim?.() ?? Response.json(claim);
    if (p === `/v1/claims/${C}/trace`) return over.trace?.() ?? Response.json(trace);
    return problem(404, "claim not found");
  });
}

const render = async (el: Promise<React.ReactElement>) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{await el}</QueryClientProvider>);
const params = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/app/claims/[id]", () => {
  it("loads the claim and its trace", async () => {
    serve();
    const out = await render(ClaimPage(params(C)));
    expect(calls.sort()).toEqual([`http://api.test/v1/claims/${C}`, `http://api.test/v1/claims/${C}/trace`]);
    expect(out).toContain("Claimant and claim");
    expect(out).toContain("Test Claimant");
    expect(out).toContain(`href="/app/programs/0874f66f-bfb5-552b-bd93-6266f49c1d5a/claims"`);
  });

  it("treats a 404 'no run for this claim' as not adjudicated, not as an error", async () => {
    serve({ claim: () => Response.json({ ...claim, status: "submitted", run: null, decision: null }), trace: () => problem(404, "no run for this claim") });
    const out = await render(ClaimPage(params(C)));
    expect(out).toContain("No decision yet. The claim waits for the adjudication worker.");
    expect(out).toContain("Not adjudicated yet");
    expect(out).not.toContain("could not be loaded");
  });

  it("still shows the claim when the trace fails", async () => {
    serve({ trace: () => problem(503, "down") });
    const out = await render(ClaimPage(params(C)));
    expect(out).toContain("The run could not be loaded");
    expect(out).toContain("Claimant and claim");
  });

  it("shows another firm's claim as not found", async () => {
    serve({ claim: () => problem(404, "claim not found") });
    const out = await render(ClaimPage(params(C)));
    expect(out).toContain("not found, or you do not have access");
    expect(out).not.toContain("Claimant and claim");
  });

  it("rejects a claim answer that breaks the contract", async () => {
    serve({ claim: () => Response.json({ id: C }) });
    expect(await render(ClaimPage(params(C)))).toContain("does not match the contract");
  });

  it("never sends a malformed claim id", async () => {
    serve();
    const out = await render(ClaimPage(params("../programs")));
    expect(calls).toHaveLength(0);
    expect(out).toContain("not found");
  });
});

describe("/app/claims/[id]/trace", () => {
  it("renders the steps with the decision's citations", async () => {
    serve({ claim: () => Response.json({ ...claim, decision: { ...(claim.decision as object), cited_rules: ["T1"] } }) });
    const out = await render(TracePage(params(C)));
    expect(out).toContain("Agent trace");
    expect(out.match(/data-step="/g)).toHaveLength(9);
    expect(out.match(/cited by the decision/g)).toHaveLength(1);
  });

  it("shows an empty state for a claim with no run", async () => {
    serve({ trace: () => problem(404, "no run for this claim") });
    expect(await render(TracePage(params(C)))).toContain("Not adjudicated yet");
  });

  it("shows a trace error with its correlation id", async () => {
    serve({ trace: () => problem(500, "boom") });
    const out = await render(TracePage(params(C)));
    expect(out).toContain("c-500");
  });
});
