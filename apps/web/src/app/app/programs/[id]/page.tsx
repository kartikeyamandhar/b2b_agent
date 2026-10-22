import type { Metadata } from "next";
import Link from "next/link";

import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramOverview } from "@/components/ProgramOverview";
import { UUID, loadProgram } from "@/lib/api/queries";
import { currentConsoleRole } from "@/lib/auth/current-role";
import { loadMe } from "@/lib/me";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Product" };

export default async function ProgramPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // The company's name heads the claim poster; without it the poster shows the product alone. A malformed id names no
  // product, so nothing is sent for it.
  const [result, role, me] = await Promise.all([loadProgram(id), currentConsoleRole(), UUID.test(id) ? loadMe() : null]);
  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Products
        </Link>
      </nav>
      {result.ok ? (
        <ProgramOverview program={result.data} canAdmin={role === "admin"} companyName={me?.ok ? (me.data.firm?.name ?? null) : null} />
      ) : (
        <ProblemAlert problem={result.problem} />
      )}
    </div>
  );
}
