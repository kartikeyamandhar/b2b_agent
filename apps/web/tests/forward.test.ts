import { describe, expect, it } from "vitest";

import { forwardRequestHeaders, forwardResponseHeaders, isAllowedMethod, resolveTarget } from "@/lib/api/forward";

describe("resolveTarget", () => {
  it("maps /v1 to the console and /portal to the portal", () => {
    expect(resolveTarget(["v1", "programs"])).toEqual({ surface: "console", path: "/v1/programs" });
    expect(resolveTarget(["portal", "dev.a.b", "submit"])).toEqual({ surface: "portal", path: "/portal/dev.a.b/submit" });
    expect(resolveTarget(["v1", "payouts", "exports", "abc.csv"])?.path).toBe("/v1/payouts/exports/abc.csv");
  });

  it.each([
    [["internal", "feeds", "pull"]],
    [["internal", "stripe", "webhook"]],
    [["dev", "console"]],
    [["docs"]],
    [["openapi.json"]],
    [["v1"]],
    [["healthz"]],
  ])("refuses %j", (segments) => {
    expect(resolveTarget(segments)).toBeNull();
  });

  it("refuses traversal and smuggled separators", () => {
    expect(resolveTarget(["v1", "..", "internal", "feeds"])).toBeNull();
    expect(resolveTarget(["v1", "."])).toBeNull();
    expect(resolveTarget(["v1", "a/../../internal"])).toBeNull();
    expect(resolveTarget(["v1", "a\\b"])).toBeNull();
    expect(resolveTarget(["v1", "a\nb"])).toBeNull();
    expect(resolveTarget(["v1", ""])).toBeNull();
  });

  it("re-encodes segments so they stay one path segment", () => {
    expect(resolveTarget(["v1", "a b?c#d"])?.path).toBe("/v1/a%20b%3Fc%23d");
  });
});

describe("headers", () => {
  const browser = new Headers({
    "content-type": "application/json",
    accept: "application/json",
    "x-dev-firm-id": "00000000-0000-4000-8000-00000000d002",
    "X-Dev-Role": "admin",
    authorization: "Bearer stolen",
    cookie: "session=1",
    "x-forwarded-for": "1.2.3.4",
    "x-request-id": "chosen-by-client",
    host: "evil.example",
  });

  it("drops identity a browser sends and attaches the server's", () => {
    const out = forwardRequestHeaders(browser, { "X-Dev-Firm-Id": "00000000-0000-4000-8000-00000000d001", "X-Dev-Role": "reviewer" }, "rid-1");
    expect(out.get("x-dev-firm-id")).toBe("00000000-0000-4000-8000-00000000d001");
    expect(out.get("x-dev-role")).toBe("reviewer");
    expect(out.get("authorization")).toBeNull();
    expect(out.get("cookie")).toBeNull();
    expect(out.get("x-forwarded-for")).toBeNull();
    expect(out.get("host")).toBeNull();
    expect(out.get("x-request-id")).toBe("rid-1");
    expect(out.get("content-type")).toBe("application/json");
  });

  it("sends no identity on portal calls", () => {
    const out = forwardRequestHeaders(browser, null, "rid-2");
    expect(out.get("x-dev-firm-id")).toBeNull();
    expect(out.get("x-dev-role")).toBeNull();
    expect(out.get("authorization")).toBeNull();
  });

  it("passes only safe response headers", () => {
    const out = forwardResponseHeaders(
      new Headers({
        "content-type": "text/csv",
        "content-disposition": "attachment; filename=batch.csv",
        "retry-after": "30",
        "set-cookie": "a=b",
        "content-length": "999",
        "content-encoding": "gzip",
        "x-request-id": "rid-3",
      }),
    );
    expect(out.get("content-type")).toBe("text/csv");
    expect(out.get("content-disposition")).toContain("batch.csv");
    expect(out.get("retry-after")).toBe("30");
    expect(out.get("x-request-id")).toBe("rid-3");
    expect(out.get("set-cookie")).toBeNull();
    expect(out.get("content-length")).toBeNull();
    expect(out.get("content-encoding")).toBeNull();
    expect(out.get("cache-control")).toBe("no-store");
  });

  it("forwards only GET, POST and PATCH", () => {
    expect(isAllowedMethod("GET") && isAllowedMethod("POST") && isAllowedMethod("PATCH")).toBe(true);
    expect(isAllowedMethod("DELETE")).toBe(false);
    expect(isAllowedMethod("PUT")).toBe(false);
  });
});

import { isSameOriginWrite } from "@/lib/api/forward";

describe("isSameOriginWrite (CSRF)", () => {
  const h = (o: Record<string, string>) => new Headers({ host: "localhost:3000", ...o });

  it("accepts same-origin browser writes and non-browser clients", () => {
    expect(isSameOriginWrite(h({ origin: "http://localhost:3000", "sec-fetch-site": "same-origin" }))).toBe(true);
    expect(isSameOriginWrite(h({}))).toBe(true);
  });

  it.each([
    [{ origin: "https://evil.example", "sec-fetch-site": "cross-site" }],
    [{ "sec-fetch-site": "same-site" }],
    [{ origin: "https://evil.example" }],
    [{ origin: "http://localhost:3001" }],
    [{ origin: "null" }],
  ])("refuses %j", (headers) => {
    expect(isSameOriginWrite(h(headers))).toBe(false);
  });
});
