import type { Metadata } from "next";
import type { ReactNode } from "react";

// The claim link is a bearer token in the URL: never send it as a referrer, never index it. No Clerk here.
export const metadata: Metadata = {
  title: { absolute: "Product recall claim" },
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-paper text-base text-ink">
      <header className="border-b border-line bg-surface px-4 py-3.5">
        <p className="mx-auto flex max-w-xl items-center gap-2 text-sm font-medium tracking-wide text-ink">
          <span aria-hidden="true" className="inline-block h-2 w-2 rotate-45 bg-copper" />
          Product recall claim
        </p>
      </header>
      <main className="mx-auto max-w-xl px-4 py-6 sm:py-10">{children}</main>
    </div>
  );
}
