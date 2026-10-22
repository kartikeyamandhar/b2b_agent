import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { welcomeBody } from "@/components/AccessGate";
import { consoleStart, meOnce, plansOnce } from "@/lib/me";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Set up your company" };

/** Setup (plan v3): the company's name and plan. A company already set up goes on to where it belongs. */
export default async function WelcomePage() {
  const me = await meOnce();
  if (me.ok) {
    const start = consoleStart(me.data);
    if (start !== "/app/welcome") redirect(start);
  }
  return welcomeBody(await plansOnce());
}
