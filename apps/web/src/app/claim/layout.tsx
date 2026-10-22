import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PortalFrame } from "@/components/portal/PortalFrame";

// The claim link is a bearer token in the URL: never send it as a referrer, never index it. No Clerk here.
export const metadata: Metadata = {
  title: { absolute: "Product recall claim" },
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: ReactNode }) {
  return <PortalFrame>{children}</PortalFrame>;
}
