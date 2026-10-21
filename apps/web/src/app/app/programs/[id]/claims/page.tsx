import Link from "next/link";

import { ClaimsQueue } from "@/components/ClaimsQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import { loadClaims, loadProgram } from "@/lib/api/queries";
import { parseQueueParams } from "@/lib/claims";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ClaimsQueuePage({ params, searchParams }: Props) {
  const { id } = await params;
  const parsed = parseQueueParams(await searchParams);
  const queue = { status: parsed.status, cursor: parsed.cursor };
  const [program, page] = await Promise.all([loadProgram(id), loadClaims(id, queue)]);
  const loadedAt = Date.now();

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-3 flex gap-1 text-xs">
        <Link href="/app/programs" className="text-accent hover:underline">
          Programs
        </Link>
        {program.ok ? (
          <>
            <span aria-hidden="true">/</span>
            <Link href={`/app/programs/${id}`} className="text-accent hover:underline">
              {program.data.title}
            </Link>
          </>
        ) : null}
      </nav>
      <h1 className="mb-3 text-lg font-semibold">Claims queue</h1>

      {parsed.ignoredStatus || parsed.ignoredCursor ? (
        <p role="status" className="mb-3 text-xs text-warn-ink" data-ignored-params>
          {parsed.ignoredStatus ? `"${parsed.ignoredStatus}" is not a claim status; showing every status. ` : ""}
          {parsed.ignoredCursor ? "The page link was not valid; showing the first page." : ""}
        </p>
      ) : null}

      {program.ok ? (
        <ClaimsQueue
          // A new filter or page is a new query: remount so the server's first load seeds it.
          key={`${queue.status ?? "all"}:${queue.cursor ?? ""}`}
          programId={id}
          params={queue}
          initialPage={page}
          initialProgram={program.data}
          loadedAt={loadedAt}
        />
      ) : (
        <ProblemAlert problem={program.problem} />
      )}
    </div>
  );
}
