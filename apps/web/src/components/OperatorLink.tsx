import Link from "next/link";

import { isOperatorQuick } from "@/lib/api/operator";

/** The way to the operator queue from a page with no console. Render it inside <Suspense fallback={null}>, so the
 *  page shows at once and the link streams in for the platform operator only. */
export async function OperatorLink({ check = isOperatorQuick }: { check?: () => Promise<boolean> }) {
  if (!(await check())) return null;
  return (
    <p className="text-sm text-ink" data-operator-link>
      You are the platform operator.{" "}
      <Link href="/operator" className="font-medium text-accent hover:underline">
        Open the operator queue
      </Link>
    </p>
  );
}
