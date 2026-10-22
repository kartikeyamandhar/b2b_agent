import { ClerkProvider } from "@clerk/nextjs";
import Link from "next/link";
import type { ReactNode } from "react";

import { clerkEnabled } from "@/lib/auth/mode";
import { CLERK_BRAND_APPEARANCE, CLERK_LOCALIZATION } from "@/lib/clerk-appearance";

import { BrandMark } from "./BrandMark";

/**
 * Clerk for the console, sign-in, sign-up, and organization pages only. The claimant portal never loads Clerk's
 * script (02-ui.md: /claim/* is outside Clerk), so the provider is per section rather than in the root layout. Every
 * widget gets the brand blue and titles that name RecallOps, not the Clerk application's dashboard name.
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
 * The frame of the sign-in, sign-up, and organization pages (and AuthOff in development auth): the RecallOps mark and
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
