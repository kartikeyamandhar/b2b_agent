import type { ReactNode } from "react";

import { BrandMark } from "@/components/BrandMark";

/** The claimant's frame for /claim and /file: a brand line and one narrow column, phone first. No Clerk and no
 *  console chrome, because claimants never have an account. */
export function PortalFrame({ children }: { children: ReactNode }) {
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
