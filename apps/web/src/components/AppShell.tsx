import Link from "next/link";
import type { ReactNode } from "react";

import { BrandMark } from "./BrandMark";
import { NavLinks } from "./NavLinks";

export type NavItem = { href: string; label: string; group: string; adminOnly?: boolean };

// Only built pages are linked. Groups give the sidebar its hierarchy.
export const CONSOLE_NAV: readonly NavItem[] = [
  { href: "/app/programs", label: "Programs", group: "Operations" },
  { href: "/app/payouts", label: "Payouts", group: "Operations", adminOnly: true },
  { href: "/app/billing", label: "Billing", group: "Account" },
  { href: "/app/access", label: "Recall access", group: "Account" },
];

/** The RecallOps wordmark, as in the README banner: the shield mark and the name in the system sans. */
export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <BrandMark size={30} />
      <span className="text-xl font-semibold tracking-tight text-ink">RecallOps</span>
    </span>
  );
}

export type OrgIdentity = { name: string; detail: string };

function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0]![0]! + words[1]![0]! : name.slice(0, 2)).toUpperCase();
}

/** Who is signed in to which organization, at the foot of the sidebar. Clerk mode adds its switcher and user menu. */
function OrgBlock({ org, identityNote, controls }: { org?: OrgIdentity; identityNote: string; controls?: ReactNode }) {
  return (
    <div className="border-t border-line px-4 py-4" data-org-block>
      <p className="eyebrow mb-2 px-2">Organization</p>
      {org ? (
        <div className="flex items-center gap-3 rounded-lg px-2 py-1.5">
          <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-accent text-xs font-semibold tracking-wide text-bg">
            {initials(org.name)}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium text-ink">{org.name}</span>
            <span className="block truncate text-xs text-ink-muted">{org.detail}</span>
          </span>
        </div>
      ) : (
        <p className="px-2 text-xs text-ink-muted [overflow-wrap:anywhere]">{identityNote}</p>
      )}
      {controls ? <div className="mt-3 px-2">{controls}</div> : null}
    </div>
  );
}

/** Console frame (02-ui.md component 1). Desktop: a full-height sidebar with brand, grouped navigation, and organization. */
export function AppShell({
  children,
  identityNote,
  controls,
  org,
  role,
}: {
  children: ReactNode;
  identityNote: string;
  controls?: ReactNode;
  org?: OrgIdentity;
  /** Hides admin-only sections from reviewers; the API still enforces every role. */
  role?: "admin" | "reviewer";
}) {
  const nav = CONSOLE_NAV.filter((i) => !i.adminOnly || role === "admin");
  return (
    <div className="flex min-h-dvh flex-col bg-paper md:flex-row">
      <nav aria-label="Console" className="border-b border-line bg-panel md:sticky md:top-0 md:flex md:h-dvh md:w-64 md:shrink-0 md:flex-col md:border-r md:border-b-0">
        <Link href="/app/programs" className="block px-6 pt-5 pb-4 md:pt-8 md:pb-7" aria-label="RecallOps, programs">
          <Wordmark />
          <span className="mt-1.5 hidden pl-10 text-xs text-ink-muted md:block">Recall operations</span>
        </Link>
        <NavLinks items={nav} />
        <div className="hidden md:mt-auto md:block">
          <OrgBlock org={org} identityNote={identityNote} controls={controls} />
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Phones: the identity and Clerk controls sit in a slim bar; desktop shows them in the sidebar. */}
        <header className="flex min-h-12 items-center justify-between gap-4 border-b border-line bg-surface px-4 py-2 text-xs text-ink-muted md:hidden">
          <span className="min-w-0 [overflow-wrap:anywhere]">{org ? `${org.name} · ${org.detail}` : identityNote}</span>
          {controls}
        </header>
        <main className="mx-auto w-full max-w-6xl min-w-0 flex-1 px-4 py-6 md:px-10 md:py-12">{children}</main>
      </div>
    </div>
  );
}
