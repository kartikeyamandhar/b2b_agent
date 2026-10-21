import type { Metadata } from "next";
import Link from "next/link";

import { FraudRings } from "@/components/FraudRings";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadFraudGraph, loadProgram } from "@/lib/api/queries";
import { GRAPH_LIMIT } from "@/lib/fraud";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Fraud rings" };

export default async function FraudRingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [program, graph] = await Promise.all([loadProgram(id), loadFraudGraph(id, GRAPH_LIMIT)]);
  return (
    <div className="grid grid-cols-1 gap-6">
      <nav aria-label="Breadcrumb" className="text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          ← Programs
        </Link>
        {program.ok ? (
          <>
            <span className="mx-1 text-ink-muted">/</span>
            <Link href={`/app/programs/${program.data.id}`} className="text-accent hover:underline">
              {program.data.title}
            </Link>
          </>
        ) : null}
      </nav>
      <header>
        <h1 className="page-title">Fraud rings</h1>
        <p className="mt-2 max-w-2xl text-ink-muted">
          Claims linked because they share a photo, an address, a serial number, or a payout destination. Rings help a
          reviewer see a pattern; each claim is still decided on its own page.
        </p>
      </header>
      {!program.ok ? (
        <ProblemAlert problem={program.problem} />
      ) : graph.ok ? (
        <FraudRings graph={graph.data} />
      ) : graph.problem.status === 503 ? (
        <ProblemAlert
          problem={graph.problem}
          showDetail={false}
          sentence="The fraud graph is temporarily unavailable. Duplicate checks still run on every claim, and each claim’s page lists its duplicates."
        />
      ) : (
        <ProblemAlert problem={graph.problem} />
      )}
    </div>
  );
}
