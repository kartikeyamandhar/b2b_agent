import Link from "next/link";

import { AuthFrame } from "./ClerkShell";

/** Shown on Clerk pages when the web app runs with development auth: the page graders see in the local demo. */
export function AuthOff() {
  return (
    <AuthFrame>
      <section aria-labelledby="auth-off-title" className="grid w-full max-w-md gap-3 rounded-xl border border-line bg-surface p-6">
        <h1 id="auth-off-title" className="page-title">
          Sign-in is off
        </h1>
        <p className="text-sm text-ink-muted">
          This web app runs with development authentication (<code className="font-mono text-ink">RECALLOPS_WEB_AUTH=dev</code>), so
          the console opens without an account. To sign in, set <code className="font-mono text-ink">RECALLOPS_WEB_AUTH=clerk</code> and
          the two Clerk keys.
        </p>
        <Link href="/app/programs" className="mt-1 w-fit rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg hover:opacity-90">
          Open the console
        </Link>
      </section>
    </AuthFrame>
  );
}
