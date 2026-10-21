// Server-side settings for talking to the API. None of these names is NEXT_PUBLIC_, so Next.js never inlines
// them into a browser bundle; modules that read them must only run on the server.

export type Env = Readonly<Record<string, string | undefined>>;

export type ApiConfig = {
  baseUrl: string;
  timeoutMs: number;
};

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** localhost, 127.0.0.1, or ::1 (a URL hostname, or a Host header with or without its port). */
export function isLoopbackHost(host: string | null | undefined): boolean {
  if (!host) return false;
  // [::1]:3000 keeps its brackets; a bare IPv6 address (two or more colons) has no port; otherwise strip the port.
  const name = host.startsWith("[") ? host.slice(0, host.indexOf("]") + 1) : (host.match(/:/g) ?? []).length > 1 ? host : host.split(":")[0]!;
  return LOOPBACK.has(name.toLowerCase());
}

export function apiConfig(env: Env = process.env): ApiConfig {
  const raw = env.RECALLOPS_API_URL ?? "http://127.0.0.1:8000";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ConfigError("RECALLOPS_API_URL is not a valid URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new ConfigError("RECALLOPS_API_URL must be http or https");
  }
  // Session tokens and claimant details travel on this hop: plain http only to this machine (review of PR 9).
  if (url.protocol === "http:" && !isLoopbackHost(url.hostname)) {
    throw new ConfigError("RECALLOPS_API_URL must use https unless it points at this machine");
  }
  const timeout = Number(env.RECALLOPS_API_TIMEOUT_MS ?? "30000");
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 120_000) {
    throw new ConfigError("RECALLOPS_API_TIMEOUT_MS must be an integer from 1000 to 120000");
  }
  return { baseUrl: url.origin + url.pathname.replace(/\/+$/, ""), timeoutMs: timeout };
}
