import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "RecallOps", template: "%s · RecallOps" },
  description: "Recall operations console and claim portal",
};
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // The browser's own chrome matches the page ground (--paper) in each mode.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#111110" },
  ],
};

// No ClerkProvider here: ClerkShell provides Clerk per section (the console through ClerkConsole, and the sign-in,
// sign-up, and organization pages), and only in Clerk mode, so the claim portal never loads Clerk.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
