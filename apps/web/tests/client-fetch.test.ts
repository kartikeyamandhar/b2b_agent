import { afterEach, describe, expect, it, vi } from "vitest";

import { ClaimPage } from "@/lib/api-types";
import { fetchJson } from "@/lib/api/client";
import { ApiError } from "@/lib/api/problem";

afterEach(() => vi.unstubAllGlobals());

const stub = (fn: (url: string, init: RequestInit) => Response | Promise<Response>) => {
  const seen: Array<{ url: string; init: RequestInit }> = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    seen.push({ url, init });
    return fn(url, init);
  });
  return seen;
};

describe("fetchJson (browser)", () => {
  it("calls the same-origin proxy and sends no identity", async () => {
    const seen = stub(() => Response.json({ items: [], next_cursor: null }));
    await fetchJson(ClaimPage, "/v1/programs/p/claims", "?limit=50");
    expect(seen[0]!.url).toBe("/api/v1/programs/p/claims?limit=50");
    const headers = new Headers(seen[0]!.init.headers);
    expect([...headers.keys()]).toEqual(["accept"]);
    expect(seen[0]!.init.cache).toBe("no-store");
  });

  it("validates the answer against the contract", async () => {
    stub(() => Response.json({ items: [{ id: 1 }], next_cursor: null }, { headers: { "x-request-id": "r-2" } }));
    const err = await fetchJson(ClaimPage, "/v1/x").catch((e: unknown) => e);
    expect((err as ApiError).problem).toMatchObject({ status: 502, title: "Contract Mismatch", correlationId: "r-2" });
  });

  it("turns a problem detail into an ApiError", async () => {
    stub(() => Response.json({ title: "Bad Request", status: 400, detail: "invalid cursor", correlation_id: "c-1" }, { status: 400 }));
    const err = await fetchJson(ClaimPage, "/v1/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).problem).toMatchObject({ status: 400, detail: "invalid cursor", correlationId: "c-1" });
  });

  it("handles a non-JSON error body", async () => {
    stub(() => new Response("upstream exploded", { status: 502 }));
    expect(((await fetchJson(ClaimPage, "/v1/x").catch((e: unknown) => e)) as ApiError).problem.status).toBe(502);
  });

  it("reports a dropped connection as status 0 and lets aborts through", async () => {
    stub(() => {
      throw new TypeError("Failed to fetch");
    });
    expect(((await fetchJson(ClaimPage, "/v1/x").catch((e: unknown) => e)) as ApiError).problem.status).toBe(0);
    stub(() => {
      throw new DOMException("aborted", "AbortError");
    });
    await expect(fetchJson(ClaimPage, "/v1/x")).rejects.toMatchObject({ name: "AbortError" });
  });
});
