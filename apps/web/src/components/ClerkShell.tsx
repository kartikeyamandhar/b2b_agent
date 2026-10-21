import { ClerkProvider } from "@clerk/nextjs";
import type { ReactNode } from "react";

import { clerkEnabled } from "@/lib/auth/mode";

/**
 * Clerk for the console, sign-in, sign-up, and organization pages only. The claimant portal never loads Clerk's
 * script (02-ui.md: /claim/* is outside Clerk), so the provider is per section rather than in the root layout.
 */
export function ClerkShell({ children }: { children: ReactNode }) {
  if (!clerkEnabled(process.env)) return <>{children}</>;
  return (
    <ClerkProvider signInUrl="/sign-in" signUpUrl="/sign-up" afterSignOutUrl="/sign-in">
      {children}
    </ClerkProvider>
  );
}
