"""The fraud graph view of one program for the console (GET /v1/programs/{id}/fraud-graph, M5).

Reads Neo4j with READ_ACCESS sessions only (the store's read methods) and treats its output as untrusted: every
claim id is re-read through the caller's RLS tenant session, statuses come from Postgres, and ids Postgres does not
return are dropped. The caller checks that the program exists first (404) and maps GraphUnavailable to a fixed 503.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from recallops.fraud.cypher import GRAPH_KINDS, GraphKind
from recallops.fraud.graph import GraphStore
from recallops.logs import get_logger, log_event
from recallops.settings import get_settings
from recallops.tenancy import FirmContext

log = get_logger("fraud.view")

EDGES_PER_NODE = 4  # edge cap = node cap x this; the view says truncated when either cap is hit

STATUS_SQL = text(
    "SELECT id, status FROM claims WHERE firm_id = :f AND program_id = :p AND status <> 'draft' AND id = ANY(:ids)"
)
KINDS_SQL = text(
    "SELECT kind, member_claim_ids FROM fraud_clusters "
    "WHERE firm_id = :f AND program_id = :p AND member_claim_ids && CAST(:ids AS uuid[])"
)


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FraudGraphNode(_View):
    claim_id: UUID
    status: str
    cluster_kinds: list[GraphKind]


class FraudGraphEdge(_View):
    a: UUID
    b: UUID
    kind: GraphKind


class FraudGraphView(_View):
    program_id: UUID
    graph: Literal["ok"] = "ok"
    nodes: list[FraudGraphNode]
    edges: list[FraudGraphEdge]
    rings: list[list[UUID]]
    truncated: bool


def _known(session: Session, ctx: FirmContext, program_id: UUID, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = session.execute(STATUS_SQL, {"f": ctx.firm_id, "p": program_id, "ids": sorted(ids, key=str)})
    return {r.id: r.status for r in rows}


def fraud_graph(
    session: Session,
    store: GraphStore,
    ctx: FirmContext,
    program_id: UUID,
    node_cap: int = 500,
    namespace: str | None = None,
) -> FraudGraphView:
    """Claims that share any identity or pHash edge with another claim of the program (rings across all kinds),
    largest rings first, at most node_cap claims; edges between the shown claims; per-node cluster kinds from
    fraud_clusters."""
    ns = namespace or get_settings().neo4j_namespace
    rings_raw = store.program_rings(ns, ctx.firm_id, program_id)
    every = set().union(*rings_raw) if rings_raw else set()
    status = _known(session, ctx, program_id, every)
    unknown = every - status.keys()
    if unknown:
        log_event(
            log,
            "graph_tenant_violation",
            level=logging.ERROR,
            firm_id=str(ctx.firm_id),
            program_id=str(program_id),
            source="fraud_graph_view",
            dropped=len(unknown),
        )
    rings = [frozenset(r & status.keys()) for r in rings_raw]
    rings = sorted((r for r in rings if len(r) >= 2), key=lambda r: (-len(r), sorted(str(x) for x in r)))

    shown: list[list[UUID]] = []
    nodes: set[UUID] = set()
    truncated = False
    for ring in rings:
        members = sorted(ring, key=str)
        if len(nodes) + len(members) <= node_cap:
            shown.append(members)
            nodes.update(members)
            continue
        truncated = True
        if not shown:  # one ring larger than the cap: show its first node_cap members
            shown.append(members[:node_cap])
            nodes.update(members[:node_cap])
        break

    edge_cap = max(1, node_cap * EDGES_PER_NODE)
    edges_raw = store.program_edges(ns, ctx.firm_id, program_id, nodes, edge_cap) if nodes else []
    if len(edges_raw) >= edge_cap:
        truncated = True
    edges = [FraudGraphEdge(a=e.a, b=e.b, kind=e.kind) for e in edges_raw if e.a in nodes and e.b in nodes]

    kinds: dict[UUID, set[GraphKind]] = {n: set() for n in nodes}
    if nodes:
        for row in session.execute(KINDS_SQL, {"f": ctx.firm_id, "p": program_id, "ids": sorted(nodes, key=str)}):
            kind = next((k for k in GRAPH_KINDS if k == row.kind), None)
            if kind is None:
                continue
            for member in row.member_claim_ids:
                if member in kinds:
                    kinds[member].add(kind)
    return FraudGraphView(
        program_id=program_id,
        nodes=[
            FraudGraphNode(claim_id=n, status=status[n], cluster_kinds=[k for k in GRAPH_KINDS if k in kinds[n]])
            for n in sorted(nodes, key=str)
        ],
        edges=edges,
        rings=shown,
        truncated=truncated,
    )
