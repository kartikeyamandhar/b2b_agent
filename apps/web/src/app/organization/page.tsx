import type { Metadata } from "next";
import { OrganizationList } from "@clerk/nextjs";

import { AuthOff } from "@/components/AuthOff";
import { ClerkShell } from "@/components/ClerkShell";
import { clerkEnabled } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Organization" };

// 04-login.md: a user without an organization creates or selects one before anything else.
export default function OrganizationPage() {
  if (!clerkEnabled(process.env)) return <AuthOff />;
  return (
    <ClerkShell>
      <main className="mx-auto grid min-h-dvh max-w-xl place-items-center p-4">
        <div className="grid gap-3">
          <h1 className="text-lg font-semibold">Choose your organization</h1>
          <p className="text-sm text-ink-muted">RecallOps works inside an organization. Create one, or pick one you belong to.</p>
          <OrganizationList hidePersonal afterSelectOrganizationUrl="/app/programs" afterCreateOrganizationUrl="/app/programs" />
        </div>
      </main>
    </ClerkShell>
  );
}
