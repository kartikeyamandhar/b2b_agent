import type { Metadata } from "next";
import { notFound } from "next/navigation";

export const metadata: Metadata = { title: "Page not found" };

/** A mistyped or stale console URL: the console's own not-found page, inside the shell (a nested not-found only
 *  renders when its segment calls notFound(); every real /app route takes precedence over this catch-all). */
export default function MissingConsolePage(): never {
  notFound();
}
