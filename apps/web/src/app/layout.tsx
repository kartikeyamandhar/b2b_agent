import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = { title: "RecallOps", description: "Recall operations console and claim portal" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

// ClerkProvider wraps the console here once component 4 lands; the claim portal stays outside Clerk.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
