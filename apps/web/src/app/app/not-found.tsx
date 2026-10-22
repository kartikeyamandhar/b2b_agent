import Link from "next/link";

/** A console page that does not exist, shown inside the console shell (sidebar and organization kept), left aligned
 *  like every other console page. It renders for notFound() under /app; the public pages have the root not-found. */
export default function ConsoleNotFound() {
  return (
    <div className="grid w-full max-w-2xl gap-6" data-console-not-found>
      <header>
        <h1 className="page-title">Page not found</h1>
        <p className="mt-2 text-ink-muted">This console page does not exist. The link may be mistyped, or the page may have moved.</p>
      </header>
      <p className="flex flex-wrap gap-2">
        <Link href="/app/programs" className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90">
          Go to programs
        </Link>
        <Link href="/" className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-panel">
          Go to the homepage
        </Link>
      </p>
    </div>
  );
}
