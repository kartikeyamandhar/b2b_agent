// Polling runs on react-query's real QueryObserver with the exact options the queue uses; timers are faked and
// visibility is driven through focusManager (what query-core reads from document.visibilityState).
import { readFileSync } from "node:fs";

import { QueryClient, QueryObserver, focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/problem";
import { QUEUE_POLL_MS } from "@/lib/claims";
import { claimsQueueQuery, programCountsQuery } from "@/lib/queries/claims-queue";

// query-core turns refetch intervals off when it decides it runs on a server (typeof window === "undefined" at
// import). Give this file a browser-like global before query-core loads; document stays absent, so the tab counts
// as visible until a test says otherwise.
vi.hoisted(() => {
  (globalThis as { window?: unknown }).window ??= globalThis;
});

const page = JSON.parse(readFileSync(new URL("./fixtures/claims-page.json", import.meta.url), "utf8")) as unknown;
const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";

let urls: string[];
let respond: () => Response;
let client: QueryClient;

beforeEach(() => {
  vi.useFakeTimers();
  urls = [];
  respond = () => Response.json(page);
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    return respond();
  });
  client = new QueryClient();
  // QueryClientProvider does this in the app: it subscribes the cache to focus and visibility changes.
  client.mount();
});

afterEach(() => {
  focusManager.setFocused(undefined);
  client.unmount();
  client.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function observe(options = claimsQueueQuery(P, { status: "flagged", cursor: null })) {
  const observer = new QueryObserver(client, options);
  const unsubscribe = observer.subscribe(() => {});
  return { observer, unsubscribe };
}

describe("claims queue polling", () => {
  it("polls every 10 seconds through the same-origin proxy", async () => {
    const { unsubscribe } = observe();
    await vi.advanceTimersByTimeAsync(0);
    expect(urls).toEqual([`/api/v1/programs/${P}/claims?status=flagged&limit=50`]);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS - 1);
    expect(urls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(urls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS * 3);
    expect(urls).toHaveLength(5);
    unsubscribe();
  });

  it("pauses while the tab is hidden and refreshes at once when it is shown", async () => {
    const { unsubscribe } = observe();
    await vi.advanceTimersByTimeAsync(0);
    focusManager.setFocused(false);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS * 6);
    expect(urls).toHaveLength(1);
    focusManager.setFocused(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(urls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    expect(urls).toHaveLength(3);
    unsubscribe();
  });

  it("stops polling when the queue unmounts", async () => {
    const { unsubscribe } = observe();
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    const seen = urls.length;
    unsubscribe();
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS * 10);
    expect(urls).toHaveLength(seen);
    // The only timer left is the cache's garbage collection of the unused query (default 5 minutes).
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(urls).toHaveLength(seen);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not refetch on mount when the server's first page is fresh", async () => {
    const { unsubscribe } = observe({ ...claimsQueueQuery(P, { status: null, cursor: null }), initialData: { items: [], next_cursor: null }, initialDataUpdatedAt: Date.now() } as never);
    await vi.advanceTimersByTimeAsync(0);
    expect(urls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    expect(urls).toHaveLength(1);
    unsubscribe();
  });

  it("keeps the last good page when a poll fails, without retry bursts, and recovers on the next tick", async () => {
    const { observer, unsubscribe } = observe();
    await vi.advanceTimersByTimeAsync(0);
    expect(observer.getCurrentResult().data?.items).toHaveLength(5);
    respond = () => Response.json({ title: "Service Unavailable", status: 503, detail: "down", correlation_id: "c-503" }, { status: 503 });
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    let r = observer.getCurrentResult();
    expect(r.error).toBeInstanceOf(ApiError);
    expect((r.error as ApiError).problem.correlationId).toBe("c-503");
    expect(r.data?.items).toHaveLength(5);
    expect(urls).toHaveLength(2);
    respond = () => Response.json(page);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    r = observer.getCurrentResult();
    expect(r.error).toBeNull();
    expect(urls).toHaveLength(3);
    unsubscribe();
  });

  it("keys each filter and page separately so a new view never shows another view's rows", () => {
    expect(claimsQueueQuery(P, { status: "flagged", cursor: null }).queryKey).not.toEqual(claimsQueueQuery(P, { status: "flagged", cursor: "x" }).queryKey);
    expect(claimsQueueQuery(P, { status: null, cursor: null }).queryKey).not.toEqual(claimsQueueQuery(P, { status: "denied", cursor: null }).queryKey);
  });

  it("polls the program counts on the same schedule", async () => {
    const program = JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")).items[0];
    respond = () => Response.json(program);
    const { unsubscribe } = observe(programCountsQuery(P) as never);
    await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS);
    expect(urls).toEqual([`/api/v1/programs/${P}`, `/api/v1/programs/${P}`]);
    unsubscribe();
  });
});
