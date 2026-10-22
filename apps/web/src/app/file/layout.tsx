import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PortalFrame } from "@/components/portal/PortalFrame";

// A product's public claim link (plan v3). It is meant to be posted and printed, but it still never travels as a
// referrer and is never indexed, so a search engine cannot list a company's live links. No Clerk here.
export const metadata: Metadata = {
  title: { absolute: "Product recall claim" },
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function ProductLinkLayout({ children }: { children: ReactNode }) {
  return <PortalFrame>{children}</PortalFrame>;
}
