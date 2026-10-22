"use client";

import { SignOutButton, UserButton } from "@clerk/nextjs";
import { useSyncExternalStore } from "react";

import { clerkAppearance } from "@/lib/clerk-appearance";

const DARK = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  const query = window.matchMedia(DARK);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** The user menu (account and sign-out). Plan v3: one account is one company, so there is no organization switcher. */
export function AccountControls() {
  // The server renders the light default; the browser switches to the dark palette after hydration.
  const dark = useSyncExternalStore(subscribe, () => window.matchMedia(DARK).matches, () => false);
  return (
    <div className="flex items-center gap-3">
      <UserButton appearance={clerkAppearance(dark)} />
    </div>
  );
}

/** A plain Sign out button, for the operator window's header. */
export function SignOutControl() {
  return (
    <SignOutButton redirectUrl="/sign-in">
      <button type="button" className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-ink" data-sign-out>
        Sign out
      </button>
    </SignOutButton>
  );
}
