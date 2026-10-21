import { SignUp } from "@clerk/nextjs";

import { AuthOff } from "@/components/AuthOff";
import { ClerkShell } from "@/components/ClerkShell";
import { clerkEnabled } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";

export default function SignUpPage() {
  if (!clerkEnabled(process.env)) return <AuthOff />;
  return (
    <ClerkShell>
      <main className="grid min-h-dvh place-items-center p-4">
        <SignUp path="/sign-up" signInUrl="/sign-in" fallbackRedirectUrl="/organization" />
      </main>
    </ClerkShell>
  );
}
