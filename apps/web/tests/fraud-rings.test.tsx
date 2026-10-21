// Phase D: fraud rings for a program (GET /v1/programs/{id}/fraud-graph), with a graceful 503.
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import FraudRingsPage from "@/app/app/programs/[id]/fraud/page";
import { FraudPanel } from "@/components/ClaimPanels";
import { FraudRings, RingDiagram } from "@/components/FraudRings";
import { FraudGraphView } from "@/lib/api-types";
import { filterRings, graphSummary, kindLabel, ringLayout, ringsFromGraph } from "@/lib/fraud";

const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const graph = FraudGraphView.parse({
  program_id: P,
  truncated: false,
  nodes: [
    { claim_id: id(1), status: "flagged", cluster_kinds: ["same_photo"] },
    { claim_id: id(2), status: "denied", cluster_kinds: ["same_photo", "same_address"] },
    { claim_id: id(3), status: "approved", cluster_kinds: ["same_address"] },
    { claim_id: id(4), status: "approved", cluster_kinds: ["same_payout"] },
    { claim_id: id(5), status: "paid", cluster_kinds: ["same_payout"] },
  ],
  edges: [
    { a: id(1), b: id(2), kind: "same_photo" },
    { a: id(2), b: id(3), kind: "same_address" },
    { a: id(4), b: id(5), kind: "same_payout" },
    { a: id(4), b: id(1), kind: "same_serial" },
  ],
  rings: [[id(5), id(4)], [id(1), id(2), id(3)], [id(9)]],
});
const program = (JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")) as { items: unknown[] }).items[0];

beforeEach(() => {
  vi.stubEnv("RECALLOPS_API_URL", "http://api.test");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("ring model", () => {
  const rings = ringsFromGraph(graph);

  it("orders rings largest first, keeps only links inside each ring, and drops single claims", () => {
    expect(rings.map((r) => r.members.length)).toEqual([3, 2]);
    expect(rings[0]!.kinds).toEqual(["same_photo", "same_address"]);
    expect(rings[1]!.edges).toEqual([{ a: id(4), b: id(5), kind: "same_payout" }]);
    expect(rings[0]!.members.map((m) => m.status)).toEqual(["flagged", "denied", "approved"]);
  });

  it("filters by what the claims share and by open review", () => {
    expect(filterRings(rings, new Set(["same_payout"]))).toHaveLength(1);
    expect(filterRings(rings, new Set())).toHaveLength(2);
    expect(filterRings(rings, new Set(), true).map((r) => r.members.length)).toEqual([3]);
  });

  it("summarises the rings", () => {
    expect(graphSummary(rings)).toEqual({ rings: 2, claims: 5, links: 3, largest: 3, awaitingReview: 1, byKind: { same_photo: 1, same_address: 1, same_payout: 1 } });
  });

  it("labels kinds, including ones a newer contract adds", () => {
    expect(kindLabel("same_mailing")).toBe("Same mailing address");
    expect(kindLabel("same_phone")).toBe("same phone");
  });

  it("lays a ring out inside its picture", () => {
    for (const n of [2, 3, 7]) {
      const pos = [...ringLayout(Array.from({ length: n }, (_, i) => id(i))).values()];
      expect(pos.every((p) => p.x > 0 && p.x < 160 && p.y > 0 && p.y < 160)).toBe(true);
    }
  });
});

describe("components", () => {
  it("renders rings, claim links, filters, and a labelled diagram", () => {
    const html = renderToStaticMarkup(<FraudRings graph={graph} />);
    expect(html).toContain('data-ring="0"');
    expect(html).toContain(`href="/app/claims/${id(1)}"`);
    expect(html).toContain('data-kind-filter="same_photo"');
    expect(html).toContain('aria-label="3 claims linked by same photo, same home address"');
    expect(html).not.toContain("data-truncated");
  });

  it("says when the graph was cut short and when there are no rings", () => {
    expect(renderToStaticMarkup(<FraudRings graph={{ ...graph, truncated: true }} />)).toContain("data-truncated");
    expect(renderToStaticMarkup(<FraudRings graph={{ ...graph, rings: [], edges: [], nodes: [] }} />)).toContain("No fraud rings");
  });

  it("skips the picture for very large rings", () => {
    const big = ringsFromGraph({ nodes: [], edges: [], rings: [Array.from({ length: 11 }, (_, i) => id(i))] })[0]!;
    expect(renderToStaticMarkup(<RingDiagram ring={big} />)).toBe("");
  });

  it("links claim detail to the program's rings when the claim has indicators", () => {
    const cluster = { id: "c1", kind: "same_photo", score: 1, member_claim_ids: [id(1), id(2)] };
    const html = renderToStaticMarkup(<FraudPanel claimId={id(1)} clusters={[cluster as never]} hits={[]} programId={P} />);
    expect(html).toContain(`href="/app/programs/${P}/fraud"`);
    expect(renderToStaticMarkup(<FraudPanel claimId={id(1)} clusters={[]} hits={[]} programId={P} />)).not.toContain("/fraud");
  });
});

describe("/app/programs/[id]/fraud", () => {
  const serve = (graphResponse: () => Response) =>
    vi.stubGlobal("fetch", async (url: string) => (url.includes("/fraud-graph") ? graphResponse() : Response.json(program)));

  it("reads the graph with the page limit and renders rings", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return url.includes("/fraud-graph") ? Response.json(graph) : Response.json(program);
    });
    const html = renderToStaticMarkup(await FraudRingsPage({ params: Promise.resolve({ id: P }) }));
    expect(urls).toContain(`http://api.test/v1/programs/${P}/fraud-graph?limit=500`);
    expect(html).toContain("Fraud rings");
    expect(html).toContain('data-ring="0"');
  });

  it("explains a graph outage without the raw detail", async () => {
    serve(() => Response.json({ title: "Service Unavailable", status: 503, detail: "the fraud graph is unavailable", correlation_id: "g-1" }, { status: 503 }));
    const html = renderToStaticMarkup(await FraudRingsPage({ params: Promise.resolve({ id: P }) }));
    expect(html).toContain("Duplicate checks still run on every claim");
    expect(html).toContain("g-1");
  });
});
