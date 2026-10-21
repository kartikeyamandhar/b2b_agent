import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { GET, POST } from "@/app/api/[...path]/route";
import { apiJson, upstream } from "@/lib/api/backend";
import { ApiError } from "@/lib/api/problem";

const ENV = {
  RECALLOPS_API_URL: "http://127.0.0.1:8000",
  RECALLOPS_WEB_AUTH: "dev",
  RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001",
  RECALLOPS_DEV_ROLE: "admin",
};

type Seen = { url: string; init: RequestInit };

function stubFetch(respond: (seen: Seen) => Response | Promise<Response>): Seen[] {
  const calls: Seen[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const seen = { url, init };
    calls.push(seen);
    return respond(seen);
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("upstream", () => {
  it("attaches the dev identity on console calls only", async () => {
    const calls = stubFetch(() => new Response("{}"));
    await upstream("console", "/v1/programs", "?limit=5", { method: "GET" }, ENV);
    await upstream("portal", "/portal/tok", "", { method: "GET" }, ENV);
    const [consoleCall, portalCall] = calls.map((c) => new Headers(c.init.headers));
    expect(calls[0]!.url).toBe("http://127.0.0.1:8000/v1/programs?limit=5");
    expect(consoleCall!.get("x-dev-firm-id")).toBe(ENV.RECALLOPS_DEV_FIRM_ID);
    expect(portalCall!.get("x-dev-firm-id")).toBeNull();
    expect(consoleCall!.get("x-request-id")).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("turns a network failure into a 502 ApiError", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    await expect(upstream("console", "/v1/programs", "", { method: "GET" }, ENV)).rejects.toMatchObject({ problem: { status: 502 } });
  });

  it("turns a timeout into a 504 ApiError", async () => {
    stubFetch(() => {
      throw new DOMException("timed out", "TimeoutError");
    });
    await expect(upstream("console", "/v1/programs", "", { method: "GET" }, ENV)).rejects.toMatchObject({ problem: { status: 504 } });
  });

  it("refuses to call the console without configured auth", async () => {
    stubFetch(() => new Response("{}"));
    await expect(upstream("console", "/v1/programs", "", { method: "GET" }, { RECALLOPS_API_URL: "http://127.0.0.1:8000" })).rejects.toThrow(/not configured/);
  });
});

describe("apiJson", () => {
  const Shape = z.looseObject({ items: z.array(z.string()) });

  it("parses with the schema", async () => {
    stubFetch(() => Response.json({ items: ["a"] }));
    await expect(apiJson(Shape, { surface: "console", path: "/v1/x" }, ENV)).resolves.toEqual({ items: ["a"] });
  });

  it("raises the API's problem on an error status", async () => {
    stubFetch(() => Response.json({ title: "Not Found", status: 404, detail: "no such claim", correlation_id: "c-7" }, { status: 404 }));
    const err = await apiJson(Shape, { surface: "console", path: "/v1/x" }, ENV).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).problem).toMatchObject({ status: 404, detail: "no such claim", correlationId: "c-7" });
  });

  it("raises a contract mismatch instead of returning a wrong shape", async () => {
    stubFetch(() => Response.json({ items: [1] }, { headers: { "x-request-id": "r-1" } }));
    const err = await apiJson(Shape, { surface: "console", path: "/v1/x" }, ENV).catch((e: unknown) => e);
    expect((err as ApiError).problem).toMatchObject({ status: 502, title: "Contract Mismatch", correlationId: "r-1" });
  });

  it("sends a JSON body with its content type", async () => {
    const calls = stubFetch(() => Response.json({ items: [] }));
    await apiJson(Shape, { surface: "console", path: "/v1/x", method: "POST", json: { n: 3 } }, ENV);
    expect(calls[0]!.init.body).toBe('{"n":3}');
    expect(new Headers(calls[0]!.init.headers).get("content-type")).toBe("application/json");
  });
});

describe("/api/[...path] route", () => {
  const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

  function useEnv() {
    for (const [k, v] of Object.entries(ENV)) vi.stubEnv(k, v);
  }

  it("refuses routes outside /v1 and /portal without calling the API", async () => {
    useEnv();
    const calls = stubFetch(() => new Response("{}"));
    const res = await POST(new Request("http://web.test/api/internal/feeds/pull", { method: "POST" }), ctx(["internal", "feeds", "pull"]));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(calls).toHaveLength(0);
  });

  it("forwards status, body and safe headers, and replaces browser identity", async () => {
    useEnv();
    const calls = stubFetch(() => new Response("id,amount\n", { status: 200, headers: { "content-type": "text/csv", "set-cookie": "a=b" } }));
    const req = new Request("http://web.test/api/v1/payouts/exports/b1.csv", {
      headers: { "x-dev-firm-id": "00000000-0000-4000-8000-00000000d002", authorization: "Bearer x" },
    });
    const res = await GET(req, ctx(["v1", "payouts", "exports", "b1.csv"]));
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("id,amount\n");
    expect(res.headers.get("set-cookie")).toBeNull();
    const sent = new Headers(calls[0]!.init.headers);
    expect(calls[0]!.url).toBe("http://127.0.0.1:8000/v1/payouts/exports/b1.csv");
    expect(sent.get("x-dev-firm-id")).toBe(ENV.RECALLOPS_DEV_FIRM_ID);
    expect(sent.get("authorization")).toBeNull();
  });

  it("answers a problem detail when the API is down", async () => {
    useEnv();
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    const res = await GET(new Request("http://web.test/api/v1/programs"), ctx(["v1", "programs"]));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { correlation_id: string; detail: string };
    expect(body.correlation_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.detail).toBe("the API could not be reached");
  });

  it("answers 503 when console auth is not configured", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "");
    stubFetch(() => new Response("{}"));
    const res = await GET(new Request("http://web.test/api/v1/programs"), ctx(["v1", "programs"]));
    expect(res.status).toBe(503);
  });

  it("refuses an oversized body before reading it", async () => {
    useEnv();
    const calls = stubFetch(() => new Response("{}"));
    const req = new Request("http://web.test/api/portal/t/photos", { method: "POST", headers: { "content-length": "20000000" }, body: "x" });
    const res = await POST(req, ctx(["portal", "t", "photos"]));
    expect(res.status).toBe(413);
    expect(calls).toHaveLength(0);
  });
});

describe("/api proxy CSRF guard", () => {
  it("refuses a cross-site POST without calling the API", async () => {
    for (const [k, v] of Object.entries(ENV)) vi.stubEnv(k, v);
    const calls = stubFetch(() => new Response("{}"));
    const req = new Request("http://localhost:3000/api/v1/claims/c/actions", {
      method: "POST",
      headers: { origin: "https://evil.example", "sec-fetch-site": "cross-site", host: "localhost:3000", "content-type": "application/json" },
      body: '{"action":"mark_fraud"}',
    });
    const res = await POST(req, { params: Promise.resolve({ path: ["v1", "claims", "c", "actions"] }) });
    expect(res.status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});
