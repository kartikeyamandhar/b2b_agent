import type { Metadata } from "next";
import Link from "next/link";

import { BrandMark } from "@/components/BrandMark";

export const metadata: Metadata = { title: "Page not found" };

/** Every unmatched URL, and any notFound() without a nearer not-found file. It renders in the root layout, so it
 *  takes the page tokens and follows light and dark mode (Next's default was pure black in dark mode). */
export default function NotFound() {
  return (
    <main className="mx-auto grid min-h-dvh max-w-md content-center justify-items-center gap-3 px-4 py-10 text-center">
      <BrandMark size={40} />
      <h1 className="page-title mt-2">Page not found</h1>
      <p className="text-ink-muted">
        Check the address. If you are filing a claim, open the full claim link the company gave you.
      </p>
      <div className="mt-3 flex flex-wrap justify-center gap-3">
        <Link href="/" className="rounded-md bg-accent px-4 py-2 font-semibold text-bg hover:opacity-90">
          Go to the homepage
        </Link>
        <Link href="/app/programs" className="rounded-md border border-line-strong bg-surface px-4 py-2 font-medium text-ink hover:border-ink">
          Open the console
        </Link>
      </div>
    </main>
  );
}
