import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // apps/web is its own npm project inside the monorepo; never infer a root from lockfiles outside it.
  turbopack: { root: dirname(fileURLToPath(import.meta.url)) },
};

export default config;
