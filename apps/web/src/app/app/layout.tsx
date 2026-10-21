import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { devIdentity } from "@/lib/api/auth-seam";
import { ConfigError } from "@/lib/api/config";

import { Providers } from "./providers";

export const dynamic = "force-dynamic";

function identityNote(): string {
  try {
    const id = devIdentity();
    return `Development auth · firm ${id.firmId} · ${id.role}`;
  } catch (err) {
    if (err instanceof ConfigError) return `Console auth not configured: ${err.message}`;
    throw err;
  }
}

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <Providers>
      <AppShell identityNote={identityNote()}>{children}</AppShell>
    </Providers>
  );
}
