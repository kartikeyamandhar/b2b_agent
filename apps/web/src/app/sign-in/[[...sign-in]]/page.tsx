import type { Metadata } from "next";
import { SignIn } from "@clerk/nextjs";

import { AuthOff } from "@/components/AuthOff";
import { AuthFrame, ClerkShell } from "@/components/ClerkShell";
import { clerkEnabled } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Sign in" };

export default function SignInPage() {
  if (!clerkEnabled(process.env)) return <AuthOff />;
  return (
    <ClerkShell>
      <AuthFrame>
        <SignIn path="/sign-in" signUpUrl="/sign-up" fallbackRedirectUrl="/app" />
      </AuthFrame>
    </ClerkShell>
  );
}
