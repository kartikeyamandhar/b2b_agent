// The program's fraud graph (GET /v1/programs/{id}/fraud-graph) as rings a reviewer can read. The graph is a
// read-only projection of Postgres (Neo4j); no claim status or payout depends on it, so a 503 here never means a
// decision was skipped. Statuses come from Postgres in the same answer.
import type { FraudGraphView } from "@/lib/api-types";

export const LINK_KINDS = ["same_photo", "same_address", "same_mailing", "same_serial", "same_payout"] as const;

const KIND_SHORT: Record<string, string> = {
  same_photo: "Same photo",
  same_address: "Same home address",
  same_mailing: "Same mailing address",
  same_serial: "Same serial number",
  same_payout: "Same payout destination",
};

export function kindLabel(kind: string): string {
  return Object.hasOwn(KIND_SHORT, kind) ? KIND_SHORT[kind]! : kind.replace(/_/g, " ");
}

/** Dash pattern per link kind, so kinds differ without colour (colour is reserved for status). */
export const KIND_DASH: Record<string, string> = {
  same_photo: "",
  same_address: "6 3",
  same_mailing: "2 3",
  same_serial: "8 3 2 3",
  same_payout: "1 5",
};

export type RingMember = { claimId: string; status: string };
export type RingEdge = { a: string; b: string; kind: string };
export type Ring = { key: string; members: RingMember[]; edges: RingEdge[]; kinds: string[] };

export const GRAPH_LIMIT = 500;
export const NEEDS_PERSON = new Set(["flagged", "under_review", "capped"]);

/** Rings with their members (status from the answer) and the links inside each, largest and most linked first. */
export function ringsFromGraph(g: Pick<FraudGraphView, "nodes" | "edges" | "rings">): Ring[] {
  const status = new Map(g.nodes.map((n) => [n.claim_id, n.status] as const));
  const rings = g.rings
    .map((ids) => [...new Set(ids)].sort())
    .filter((ids) => ids.length > 1)
    .map((ids) => {
      const inRing = new Set(ids);
      const edges = g.edges
        .filter((e) => inRing.has(e.a) && inRing.has(e.b) && e.a !== e.b)
        .map((e) => ({ a: e.a, b: e.b, kind: String(e.kind) }));
      const kinds = [...new Set(edges.map((e) => e.kind))].sort((x, y) => order(x) - order(y));
      return { key: ids.join(":"), members: ids.map((claimId) => ({ claimId, status: status.get(claimId) ?? "unknown" })), edges, kinds };
    });
  return rings.sort((x, y) => y.members.length - x.members.length || y.edges.length - x.edges.length || x.key.localeCompare(y.key));
}

const order = (k: string) => {
  const i = (LINK_KINDS as readonly string[]).indexOf(k);
  return i < 0 ? LINK_KINDS.length : i;
};

/** Keeps rings joined by at least one of the chosen kinds; no choice keeps every ring. */
export function filterRings(rings: readonly Ring[], kinds: ReadonlySet<string>, onlyOpen = false): Ring[] {
  return rings.filter(
    (r) => (kinds.size === 0 || r.kinds.some((k) => kinds.has(k))) && (!onlyOpen || r.members.some((m) => NEEDS_PERSON.has(m.status))),
  );
}

export type GraphSummary = { rings: number; claims: number; links: number; largest: number; awaitingReview: number; byKind: Record<string, number> };

export function graphSummary(rings: readonly Ring[]): GraphSummary {
  const claims = new Set<string>();
  const byKind: Record<string, number> = {};
  let links = 0;
  let largest = 0;
  let awaitingReview = 0;
  for (const r of rings) {
    for (const m of r.members) claims.add(m.claimId);
    links += r.edges.length;
    largest = Math.max(largest, r.members.length);
    if (r.members.some((m) => NEEDS_PERSON.has(m.status))) awaitingReview += 1;
    for (const k of r.kinds) byKind[k] = (byKind[k] ?? 0) + 1;
  }
  return { rings: rings.length, claims: claims.size, links, largest, awaitingReview, byKind };
}

/** Node positions on a circle for a small ring diagram (viewBox 0 0 size size). */
export function ringLayout(ids: readonly string[], size = 160): Map<string, { x: number; y: number }> {
  const c = size / 2;
  const r = ids.length <= 2 ? size * 0.3 : size * 0.36;
  return new Map(
    ids.map((id, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / ids.length + (ids.length === 2 ? Math.PI / 2 : 0);
      return [id, { x: Math.round((c + r * Math.cos(a)) * 10) / 10, y: Math.round((c + r * Math.sin(a)) * 10) / 10 }];
    }),
  );
}
