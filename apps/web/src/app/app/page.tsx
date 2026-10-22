import { redirect } from "next/navigation";

import { consoleStart, meOnce } from "@/lib/me";

export const dynamic = "force-dynamic";

/** /app (where sign-in and sign-up land): setup until the company is set up, Recall access until the operator
 *  approves it, then its products. When GET /v1/me cannot be read, the layout shows why. */
export default async function ConsoleHome(): Promise<never> {
  const me = await meOnce();
  redirect(me.ok ? consoleStart(me.data) : "/app/programs");
}
