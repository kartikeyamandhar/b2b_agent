// Recall search (GET /v1/recalls) for Recall access. Plan v3: the console no longer creates products itself; the
// operator's approval of a recall request creates and opens one.
export const RECALL_PAGE_LIMIT = 25;

/** Query string for the recall search; an empty query lists the newest recalls. */
export function recallSearch(q: string, since: string, limit = RECALL_PAGE_LIMIT): string {
  const params = new URLSearchParams();
  const query = q.trim().slice(0, 200);
  if (query) params.set("q", query);
  if (/^\d{4}-\d{2}-\d{2}$/.test(since.trim())) params.set("since", since.trim());
  params.set("limit", String(Math.min(Math.max(1, Math.trunc(limit)), 100)));
  return `?${params.toString()}`;
}
