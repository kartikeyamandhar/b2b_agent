import type { Metadata } from "next";
import { SignUp } from "@clerk/nextjs";

import { AuthOff } from "@/components/AuthOff";
import { AuthFrame, ClerkShell } from "@/components/ClerkShell";
import { clerkEnabled } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Sign up" };

export default function SignUpPage() {
  if (!clerkEnabled(process.env)) return <AuthOff />;
  return (
    <ClerkShell>
      <AuthFrame>
        <SignUp path="/sign-up" signInUrl="/sign-in" fallbackRedirectUrl="/organization" />
      </AuthFrame>
    </ClerkShell>
  );
}
