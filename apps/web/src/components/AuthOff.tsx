import Link from "next/link";

/** Shown on Clerk pages when the web app runs with development auth. */
export function AuthOff() {
  return (
    <main className="mx-auto max-w-md p-6 text-sm">
      <h1 className="text-lg font-semibold">Sign-in is off</h1>
      <p className="mt-2 text-ink-muted">
        This web app runs with development authentication (RECALLOPS_WEB_AUTH=dev). Set RECALLOPS_WEB_AUTH=clerk with
        the Clerk keys to sign in.
      </p>
      <Link href="/app/programs" className="mt-3 inline-block text-accent underline">
        Open the console
      </Link>
    </main>
  );
}
