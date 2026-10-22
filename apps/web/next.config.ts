import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // apps/web is its own npm project inside the monorepo; never infer a root from lockfiles outside it.
  turbopack: { root: dirname(fileURLToPath(import.meta.url)) },
  // No page may be framed (clickjacking on reviewer and payout buttons); claim pages and product claim links (plan v3)
  // also never send a referrer or get indexed, as headers rather than only meta tags (04-login.md section 5; review
  // of PR 9).
  async headers() {
    const always = [
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
    ];
    const linkInUrl = [
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Robots-Tag", value: "noindex, nofollow" },
    ];
    return [
      { source: "/:path*", headers: always },
      { source: "/claim/:path*", headers: linkInUrl },
      { source: "/file/:path*", headers: linkInUrl },
    ];
  },
};

export default config;
