"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { NavItem } from "./AppShell";

/** Claims belong to programs, so a claim page marks Programs. */
const ALSO: Record<string, readonly string[]> = { "/app/programs": ["/app/claims"] };

/** Console navigation, grouped on desktop, with the current section marked (aria-current and a brand rule). */
export function NavLinks({ items }: { items: readonly NavItem[] }) {
  const path = usePathname() ?? "";
  const groups = [...new Set(items.map((i) => i.group))];
  return (
    <div className="flex gap-1 px-3 pb-3 md:block md:space-y-6 md:px-3 md:pb-6">
      {groups.map((group) => (
        <div key={group} className="contents md:block">
          <p className="eyebrow hidden px-3 pb-2 md:block">{group}</p>
          <ul className="contents md:flex md:flex-col md:gap-0.5">
            {items
              .filter((i) => i.group === group)
              .map((item) => {
                const prefixes = [item.href, ...(ALSO[item.href] ?? [])];
                const current = prefixes.some((p) => path === p || path.startsWith(`${p}/`));
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={current ? "page" : undefined}
                      className={`flex items-center rounded-md px-3 py-2 text-sm transition-colors md:rounded-l-none md:border-l-2 ${
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
