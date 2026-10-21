import Link from "next/link";
import type { ReactNode } from "react";

export type NavItem = { href: string; label: string };

// Billing and settings join in later phases; only built pages are linked.
export const CONSOLE_NAV: readonly NavItem[] = [{ href: "/app/programs", label: "Programs" }];

/** Console frame (02-ui.md component 1). The org switcher and user menu are component 4's (Clerk). */
export function AppShell({ children, identityNote }: { children: ReactNode; identityNote: string }) {
  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <nav aria-label="Console" className="border-b border-line bg-panel md:w-52 md:shrink-0 md:border-r md:border-b-0">
        <div className="px-4 py-3 text-sm font-semibold text-ink">RecallOps</div>
        <ul className="flex gap-1 px-2 pb-2 md:flex-col">
          {CONSOLE_NAV.map((item) => (
            <li key={item.href}>
              <Link href={item.href} className="block rounded px-2 py-1.5 text-sm text-ink hover:bg-line/50 focus-visible:outline-2 focus-visible:outline-accent">
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-line px-4 py-2 text-xs text-ink-muted">
          <span>{identityNote}</span>
          {/* Org switcher and user menu: component 4 (Clerk). */}
        </header>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>
    </div>
  );
}
