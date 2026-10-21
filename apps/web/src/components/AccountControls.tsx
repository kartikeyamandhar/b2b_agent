"use client";

import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";
import { useSyncExternalStore } from "react";

import { clerkAppearance } from "@/lib/clerk-appearance";

const DARK = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  const query = window.matchMedia(DARK);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Organization switcher and user menu (04-login.md: organization switcher in the app shell). */
export function AccountControls() {
  // The server renders the light default; the browser switches to the dark palette after hydration.
  const dark = useSyncExternalStore(subscribe, () => window.matchMedia(DARK).matches, () => false);
  const appearance = clerkAppearance(dark);
  return (
    <div className="flex items-center gap-3">
      <OrganizationSwitcher
        hidePersonal
        afterSelectOrganizationUrl="/app/programs"
        afterCreateOrganizationUrl="/app/programs"
        appearance={appearance}
      />
      <UserButton appearance={appearance} />
    </div>
  );
}
