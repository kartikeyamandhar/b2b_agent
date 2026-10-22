"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import type { NavItem } from "./AppShell";

/** Claims belong to products, so a claim page marks Products. */
const ALSO: Record<string, readonly string[]> = { "/app/programs": ["/app/claims"] };

const under = (path: string, prefix: string) => path === prefix || path.startsWith(`${prefix}/`);

/** The item the path belongs to: its own page first, then an item that stands for other paths too (`also`), so a
 *  gated console marks Billing on the billing page and Recall access on every page that shows the access form. */
export function currentHref(items: readonly NavItem[], path: string): string | null {
  const own = items.find((i) => under(path, i.href));
  if (own) return own.href;
  return items.find((i) => (i.also ?? ALSO[i.href] ?? []).some((p) => under(path, p)))?.href ?? null;
}

/**
 * Console navigation, grouped on desktop, with the current section marked (aria-current and a brand rule). On phones
 * the items form one row that wraps onto a second line when they do not fit, so the page never scrolls sideways and
 * a label never splits across two lines.
 */
export function NavLinks({ items }: { items: readonly NavItem[] }) {
  const path = usePathname() ?? "";
  const groups = [...new Set(items.map((i) => i.group))];
  const currentItem = currentHref(items, path);
  return (
    <div className="flex flex-wrap gap-1 px-3 pb-3 md:block md:space-y-6 md:px-3 md:pb-6">
      {groups.map((group) => (
        <div key={group} className="contents md:block">
          <p className="eyebrow hidden px-3 pb-2 md:block">{group}</p>
          <ul className="contents md:flex md:flex-col md:gap-0.5">
            {items
              .filter((i) => i.group === group)
              .map((item) => {
                const current = item.href === currentItem;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={current ? "page" : undefined}
                      className={`flex items-center rounded-md px-3 py-2 text-sm whitespace-nowrap transition-colors md:rounded-l-none md:border-l-2 md:whitespace-normal ${
                        current ? "bg-surface font-medium text-ink md:border-l-brand" : "text-ink-muted hover:bg-surface hover:text-ink md:border-l-transparent"
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * The page for a path the gate allows, and `fallback` in its place everywhere else. A server layout does not know
 * its path, so the console's setup and approval gates choose here: an unapproved company still reaches Billing, and a
 * Checkout return still lands on /app/billing/return.
 */
export function PathGate({ allow, fallback, children }: { allow: readonly string[]; fallback: ReactNode; children: ReactNode }) {
  const path = usePathname() ?? "";
  return <>{allow.some((p) => under(path, p)) ? children : fallback}</>;
}
