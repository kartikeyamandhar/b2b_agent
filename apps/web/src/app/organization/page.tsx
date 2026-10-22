import type { Metadata } from "next";
import { OrganizationList } from "@clerk/nextjs";
import { Suspense } from "react";

import { AuthOff } from "@/components/AuthOff";
import { AuthFrame, ClerkShell } from "@/components/ClerkShell";
import { OperatorLink } from "@/components/OperatorLink";
import { clerkEnabled } from "@/lib/auth/mode";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Organization" };

// 04-login.md: a user without an organization creates or selects one before anything else.
export default function OrganizationPage() {
  if (!clerkEnabled(process.env)) return <AuthOff />;
  return (
    <ClerkShell>
      <AuthFrame>
        <div className="grid w-full max-w-xl gap-3">
          <h1 className="page-title">Choose your organization</h1>
          <p className="text-sm text-ink-muted">RecallOps works inside an organization. Create one, or pick one you belong to.</p>
          <OrganizationList hidePersonal afterSelectOrganizationUrl="/app/programs" afterCreateOrganizationUrl="/app/programs" />
          {/* Streams in for the platform operator only, who needs no organization; the page never waits for it. */}
          <Suspense fallback={null}>
            <OperatorLink />
          </Suspense>
        </div>
      </AuthFrame>
    </ClerkShell>
  );
}
