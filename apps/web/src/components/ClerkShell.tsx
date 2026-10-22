import { ClerkProvider } from "@clerk/nextjs";
import Link from "next/link";
import type { ReactNode } from "react";

import { clerkEnabled } from "@/lib/auth/mode";
import { CLERK_BRAND_APPEARANCE, CLERK_LOCALIZATION } from "@/lib/clerk-appearance";

import { BrandMark } from "./BrandMark";

/**
 * Clerk for the console, the operator window, and the sign-in and sign-up pages only. The claimant portal never loads
 * Clerk's script (02-ui.md: /claim/* is outside Clerk), so the provider is per section rather than in the root layout.
 * Every widget gets the brand blue and titles that name RecallOps, not the Clerk application's dashboard name.
 */
export function ClerkShell({ children }: { children: ReactNode }) {
  if (!clerkEnabled(process.env)) return <>{children}</>;
  return (
    <ClerkProvider
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      afterSignOutUrl="/sign-in"
      appearance={CLERK_BRAND_APPEARANCE}
      localization={CLERK_LOCALIZATION}
    >
      {children}
    </ClerkProvider>
  );
}

/**
 * The frame of the sign-in and sign-up pages (and AuthOff in development auth): the RecallOps mark and
 * name above the content, linking home, centered on the page. Clerk's card carries no brand of its own. The link sits
 * in a header outside main, so the global main-link underline does not apply to it.
 */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh content-center justify-items-center gap-6 px-4 py-10">
      <header>
        <Link href="/" className="flex items-center gap-2.5 rounded-md" data-auth-brand>
          <BrandMark size={30} />
          <span className="text-[1.0625rem] font-semibold tracking-[-0.01em] text-ink">RecallOps</span>
        </Link>
      </header>
      <main className="grid w-full justify-items-center">{children}</main>
    </div>
  );
}

/**
 * The operator window's frame (plan v3): standalone, with no console sidebar, because the operator has no company.
 * A header with the RecallOps mark, "Operator", the account email, and the sign-out control, then the page.
 */
export function OperatorFrame({ email, note, controls, children }: { email: string | null; note?: string; controls?: ReactNode; children: ReactNode }) {
  const who = email ?? note;
  return (
    <div className="flex min-h-dvh flex-col bg-paper" data-operator-window>
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 md:px-10">
          <Link href="/operator" className="flex items-center gap-2.5 rounded-md" aria-label="RecallOps operator window">
            <BrandMark size={28} />
            <span className="text-[1.0625rem] font-semibold tracking-[-0.01em] text-ink">RecallOps</span>
            <span className="rounded-full border border-line-strong bg-surface px-2 py-0.5 text-xs font-medium text-ink">Operator</span>
          </Link>
          <div className="flex min-w-0 items-center gap-3">
            {who ? (
              <span className="min-w-0 text-sm text-ink-muted [overflow-wrap:anywhere]" data-operator-email>
                {who}
              </span>
            ) : null}
            {controls}
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl min-w-0 flex-1 px-4 py-6 md:px-10 md:py-10">{children}</main>
    </div>
  );
}
