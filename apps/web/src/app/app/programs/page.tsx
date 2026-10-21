import { EmptyState } from "@/components/States";

export default function ProgramsPage() {
  return (
    <section aria-labelledby="programs-title">
      <h1 id="programs-title" className="mb-4 text-lg font-semibold">
        Programs
      </h1>
      <EmptyState title="The programs list arrives in Phase 1">
        The API client and proxy are in place; this page will read GET /v1/programs.
      </EmptyState>
    </section>
  );
}
