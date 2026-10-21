import type { Metadata } from "next";
import type { ReactNode } from "react";

// The claim link is a bearer token in the URL: never send it as a referrer, never index it. No Clerk here.
export const metadata: Metadata = {
  title: "Product recall claim",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-bg text-base">
      <header className="border-b border-line px-4 py-3">
        <p className="mx-auto max-w-xl text-base font-semibold">Product recall claim</p>
      </header>
      <main className="mx-auto max-w-xl px-4 py-5">{children}</main>
    </div>
  );
}
