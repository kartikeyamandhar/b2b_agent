import type { Metadata } from "next";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/BrandMark";

// The claim link is a bearer token in the URL: never send it as a referrer, never index it. No Clerk here.
export const metadata: Metadata = {
  title: { absolute: "Product recall claim" },
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-paper text-base text-ink">
      {/* The padding sits inside the max-width box, as in main below, so the brand lines up with the content. */}
      <header className="border-b border-line bg-surface">
        <p className="mx-auto flex max-w-xl items-center gap-2 px-4 py-3.5 text-sm font-medium tracking-wide text-ink">
          <BrandMark size={22} />
          Product recall claim
        </p>
      </header>
      <main className="mx-auto max-w-xl px-4 py-6 sm:py-10">{children}</main>
    </div>
  );
}
