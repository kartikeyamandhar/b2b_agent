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
  const timeout = Number(env.RECALLOPS_API_TIMEOUT_MS ?? "30000");
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 120_000) {
    throw new ConfigError("RECALLOPS_API_TIMEOUT_MS must be an integer from 1000 to 120000");
  }
  return { baseUrl: url.origin + url.pathname.replace(/\/+$/, ""), timeoutMs: timeout };
}
