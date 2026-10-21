import { describe, expect, it } from "vitest";

import { consoleAuthHeaders, devIdentity } from "@/lib/api/auth-seam";
import { apiConfig, ConfigError } from "@/lib/api/config";

const DEV = {
  RECALLOPS_WEB_AUTH: "dev",
  RECALLOPS_DEV_FIRM_ID: "00000000-0000-4000-8000-00000000d001",
  RECALLOPS_DEV_ROLE: "reviewer",
};

describe("dev auth seam", () => {
  it("builds the dev provider headers from server settings", async () => {
    expect(await consoleAuthHeaders(DEV)).toEqual({
      "X-Dev-Firm-Id": "00000000-0000-4000-8000-00000000d001",
      "X-Dev-Role": "reviewer",
      "X-Dev-User-Id": "dev-user",
    });
  });

  it("defaults the role to admin", () => {
    expect(devIdentity({ ...DEV, RECALLOPS_DEV_ROLE: undefined }).role).toBe("admin");
  });

  it.each([
    [{}],
    [{ ...DEV, RECALLOPS_WEB_AUTH: "clerk" }],
    [{ ...DEV, RECALLOPS_WEB_AUTH: undefined }],
    [{ ...DEV, RECALLOPS_DEV_FIRM_ID: "not-a-uuid" }],
    [{ ...DEV, RECALLOPS_DEV_ROLE: "owner" }],
    [{ ...DEV, RECALLOPS_DEV_USER_ID: "a b\nc" }],
    [{ ...DEV, VERCEL_ENV: "production" }],
  ])("fails closed for %j", (env) => {
    expect(() => devIdentity(env)).toThrow(ConfigError);
  });
});

describe("apiConfig", () => {
  it("defaults to the local API and trims a trailing slash", () => {
    expect(apiConfig({})).toEqual({ baseUrl: "http://127.0.0.1:8000", timeoutMs: 30000 });
    expect(apiConfig({ RECALLOPS_API_URL: "https://api.example.com/base/" }).baseUrl).toBe("https://api.example.com/base");
  });

  it.each([
    [{ RECALLOPS_API_URL: "not a url" }],
    [{ RECALLOPS_API_URL: "file:///etc/passwd" }],
    [{ RECALLOPS_API_TIMEOUT_MS: "5" }],
    [{ RECALLOPS_API_TIMEOUT_MS: "abc" }],
  ])("refuses %j", (env) => {
    expect(() => apiConfig(env)).toThrow(ConfigError);
  });
});
