import { readFileSync } from "node:fs";

import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { claimDetailQuery, fetchTrace, traceQuery } from "@/lib/queries/claim";

// See claims-polling.test.ts: query-core disables intervals unless it sees a window at import.
vi.hoisted(() => {
  (globalThis as { window?: unknown }).window ??= globalThis;
});

const read = (f: string) => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8")) as Record<string, unknown>;
const claim = read("claim-detail.json");
const trace = read("trace-agent.json");
const C = claim.id as string;

let urls: string[];
let claimRun: string;
let client: QueryClient;

beforeEach(() => {
  vi.useFakeTimers();
  urls = [];
  claimRun = "done";
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    if (url.endsWith("/trace")) return Response.json({ ...trace, run: { ...(trace.run as object), status: claimRun } });
    return Response.json({ ...claim, run: { ...(claim.run as object), graph: "adjudicate", status: claimRun } });
  });
  client = new QueryClient();
  client.mount();
});
afterEach(() => {
  client.unmount();
  client.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("claim detail polling", () => {
  it("does not poll a settled claim", async () => {
    const o = new QueryObserver(client, claimDetailQuery(C, () => 0) as never);
    const off = o.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(20_000);
    expect(urls).toHaveLength(1);
    off();
  });

  it("polls every 2 s while the run is running and stops when it finishes", async () => {
    claimRun = "running";
    const o = new QueryObserver(client, claimDetailQuery(C, () => 0) as never);
    const off = o.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(4_000);
    expect(urls).toHaveLength(3);
    claimRun = "done";
    await vi.advanceTimersByTimeAsync(2_000);
    const settled = urls.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(urls).toHaveLength(settled);
    off();
  });

  it("polls while a queued review waits for the worker, then stops", async () => {
    const until = Date.now() + 6_000;
    const o = new QueryObserver(client, claimDetailQuery(C, () => until) as never);
    const off = o.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(6_000);
    expect(urls.length).toBeGreaterThanOrEqual(3);
    const n = urls.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(urls).toHaveLength(n);
    off();
  });
});

describe("trace polling", () => {
  it("polls every 1.5 s while running, then stops (trace.md)", async () => {
    claimRun = "running";
    const o = new QueryObserver(client, traceQuery(C) as never);
    const off = o.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(3_000);
    expect(urls).toEqual([`/api/v1/claims/${C}/trace`, `/api/v1/claims/${C}/trace`, `/api/v1/claims/${C}/trace`]);
    claimRun = "waiting_human";
    await vi.advanceTimersByTimeAsync(1_500);
    const n = urls.length;
    await vi.advanceTimersByTimeAsync(15_000);
    expect(urls).toHaveLength(n);
    off();
  });

  it("maps 'no run for this claim' to null and keeps other 404s as errors", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ title: "Not Found", status: 404, detail: "no run for this claim" }, { status: 404 }));
    await expect(fetchTrace(C)).resolves.toBeNull();
    vi.stubGlobal("fetch", async () => Response.json({ title: "Not Found", status: 404, detail: "claim not found" }, { status: 404 }));
    await expect(fetchTrace(C)).rejects.toMatchObject({ problem: { status: 404 } });
  });
});
