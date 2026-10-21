// Security findings from the backend owner's review of PR 9: the claimant's address for the API's rate limit,
// plain-http API URLs, the dev identity outside this machine, and response security headers.
import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../next.config";
import { devIdentity } from "@/lib/api/auth-seam";
import { upstream } from "@/lib/api/backend";
import { apiConfig, ConfigError, isLoopbackHost } from "@/lib/api/config";
import { clientAddress, forwardRequestHeaders, portalClientHeaders } from "@/lib/api/forward";

const SECRET = "s".repeat(40);
const ENV = {
  RECALLOPS_API_URL: "http://127.0.0.1:8000",
  RECALLOPS_WEB_AUTH: "dev",
  RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001",
  RECALLOPS_DEV_ROLE: "admin",
  RECALLOPS_WEB_PROXY_SECRET: SECRET,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the claimant's address on portal calls", () => {
  it("reads the edge's address and refuses anything that is not IP-shaped", () => {
    expect(clientAddress(new Headers({ "x-real-ip": "198.51.100.4", "x-forwarded-for": "10.1.1.1" }))).toBe("198.51.100.4");
    expect(clientAddress(new Headers({ "x-forwarded-for": "203.0.113.9, 10.0.0.1" }))).toBe("203.0.113.9");
    expect(clientAddress(new Headers({ "x-forwarded-for": "2001:db8::7" }))).toBe("2001:db8::7");
    expect(clientAddress(new Headers({ "x-real-ip": "everyone; drop" }))).toBeNull();
    expect(clientAddress(new Headers())).toBeNull();
  });

  it("names the claimant only when both the secret and an address are known", () => {
    expect(portalClientHeaders("198.51.100.4", SECRET)).toEqual({ "x-recallops-client-ip": "198.51.100.4", "x-recallops-web-secret": SECRET });
    expect(portalClientHeaders(null, SECRET)).toEqual({});
    expect(portalClientHeaders("198.51.100.4", undefined)).toEqual({});
    expect(portalClientHeaders("198.51.100.4", "")).toEqual({});
  });

  it("sends it on portal calls only, and a browser cannot send its own", async () => {
    const seen: Headers[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      seen.push(new Headers(init.headers));
      return new Response("{}");
    });
    const browser = new Headers({ "x-real-ip": "198.51.100.4", "x-recallops-client-ip": "6.6.6.6", "x-recallops-web-secret": "forged" });
    await upstream("portal", "/portal/tok", "", { method: "GET", headers: browser }, ENV);
    await upstream("console", "/v1/programs", "", { method: "GET", headers: browser }, ENV);
    expect(seen[0]!.get("x-recallops-client-ip")).toBe("198.51.100.4");
    expect(seen[0]!.get("x-recallops-web-secret")).toBe(SECRET);
    expect(seen[1]!.get("x-recallops-client-ip")).toBeNull();
    expect(seen[1]!.get("x-recallops-web-secret")).toBeNull();
    // the allowlist alone already drops the browser's copies
    expect(forwardRequestHeaders(browser, null, "r").get("x-recallops-web-secret")).toBeNull();
  });
});

describe("the API address", () => {
  it("allows plain http only to this machine", () => {
    expect(apiConfig({ RECALLOPS_API_URL: "http://127.0.0.1:8000" }).baseUrl).toBe("http://127.0.0.1:8000");
    expect(apiConfig({ RECALLOPS_API_URL: "http://localhost:8000" }).baseUrl).toBe("http://localhost:8000");
    expect(apiConfig({ RECALLOPS_API_URL: "https://api.recallops.example" }).baseUrl).toBe("https://api.recallops.example");
    expect(() => apiConfig({ RECALLOPS_API_URL: "http://recallops-api.onrender.com" })).toThrow(ConfigError);
  });

  it("knows which hosts are loopback", () => {
    for (const host of ["localhost", "localhost:3000", "127.0.0.1:3000", "[::1]:3000", "::1"]) expect(isLoopbackHost(host), host).toBe(true);
    for (const host of ["192.168.1.20:3000", "localhost.example", "127.0.0.1.nip.io", null, ""]) expect(isLoopbackHost(host), String(host)).toBe(false);
  });
});

describe("the dev identity", () => {
  it("refuses any deployment and any API on another machine", () => {
    expect(devIdentity(ENV).role).toBe("admin");
    expect(() => devIdentity({ ...ENV, VERCEL_ENV: "preview" })).toThrow(/deployment/);
    expect(() => devIdentity({ ...ENV, VERCEL_ENV: "production" })).toThrow(/deployment/);
    expect(() => devIdentity({ ...ENV, RECALLOPS_API_URL: "https://api.recallops.example" })).toThrow(/this machine/);
  });
});

describe("response security headers", () => {
  it("forbids framing everywhere and sends no referrer from claim pages", async () => {
    const rules = await nextConfig.headers!();
    const all = rules.find((r) => r.source === "/:path*")!.headers.map((h) => `${h.key}: ${h.value}`);
    expect(all).toEqual(expect.arrayContaining(["Content-Security-Policy: frame-ancestors 'none'", "X-Frame-Options: DENY", "X-Content-Type-Options: nosniff"]));
    const claim = rules.find((r) => r.source === "/claim/:path*")!.headers.map((h) => `${h.key}: ${h.value}`);
    expect(claim).toEqual(expect.arrayContaining(["Referrer-Policy: no-referrer", "X-Robots-Tag: noindex, nofollow"]));
  });
});
