"""Consistency through Postgres: on the 300 golden claims, the graph's per-kind components (and the fraud_clusters
rows sync_program writes) equal the connected components of the frozen check_duplicates hits.

Pre-registered (plan WP1 Revision 1, before this test first ran): 36 per-kind components, all of size 2:
13 same_serial, 12 same_payout, 7 same_photo, 4 same_address (0 same_mailing: golden claims carry no mailing
address); and 36 rings of size 2.
"""

from __future__ import annotations

import uuid
from collections import Counter
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops.claims.context import program_claims
from recallops.claims.tools import ClaimFacts, check_duplicates
from recallops.db.session import tenant_session
from recallops.fraud.cypher import GRAPH_KINDS
from recallops.fraud.graph import Neo4jGraphStore
from recallops.fraud.sync import NIL, sync_program
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from tests.fixtures.fraud_claims import NS, golden_claims, insert_claims, insert_firm

pytestmark = [pytest.mark.db, pytest.mark.graph]

EXPECTED = {"same_serial": 13, "same_payout": 12, "same_photo": 7, "same_address": 4, "same_mailing": 0}
EXPECTED_RINGS = 36


def union_find(population: list[ClaimFacts], kind: str) -> set[frozenset[UUID]]:
    parent = {f.claim_id: f.claim_id for f in population}

    def find(x: UUID) -> UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for claim in population:
        for hit in check_duplicates(claim, population, get_settings().phash_duplicate_bits).hits:
            if hit.kind == kind:
                parent[find(claim.claim_id)] = find(hit.other_claim_id)
    groups: dict[UUID, set[UUID]] = {}
    for f in population:
        groups.setdefault(find(f.claim_id), set()).add(f.claim_id)
    return {frozenset(g) for g in groups.values() if len(g) >= 2}


def test_golden_components_equal_check_duplicates_components(
    clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    ns = settings.neo4j_namespace
    firm = uuid.uuid5(NS, "golden-firm")
    by_program = golden_claims()
    programs = {key: uuid.uuid5(NS, f"golden-program:{key}") for key in sorted(by_program)}
    with clean_db.begin() as conn:
        insert_firm(conn, firm, "Golden", list(programs.values()))
        for key, pid in programs.items():
            insert_claims(conn, firm, pid, by_program[key])
    assert sum(len(v) for v in by_program.values()) == 300

    for pid in programs.values():
        summary = sync_program(graph_store, clean_db, app_engine_test, settings, firm, pid)
        assert summary["dropped"] == 0 and summary["synced"] == summary["claims"]

    graph: dict[str, set[frozenset[UUID]]] = {k: set() for k in GRAPH_KINDS}
    expected: dict[str, set[frozenset[UUID]]] = {k: set() for k in GRAPH_KINDS}
    rings: list[frozenset[UUID]] = []
    ctx = FirmContext(firm, "test", "admin")
    with tenant_session(ctx, app_engine_test) as session:
        for pid in programs.values():
            population = program_claims(session, firm, pid, NIL)
            for kind in GRAPH_KINDS:
                graph[kind] |= set(graph_store.program_components(ns, firm, pid, kind))
                expected[kind] |= union_find(population, kind)
            rings += graph_store.program_rings(ns, firm, pid)
    with clean_db.connect() as conn:
        rows = conn.execute(
            text("SELECT kind, member_claim_ids FROM fraud_clusters WHERE firm_id = :f"), {"f": firm}
        ).all()
    stored: dict[str, set[frozenset[UUID]]] = {k: set() for k in GRAPH_KINDS}
    for kind, members in rows:
        stored[kind].add(frozenset(members))

    differences = {k: graph[k] ^ expected[k] for k in GRAPH_KINDS if graph[k] != expected[k]}
    assert differences == {}
    assert stored == graph
    assert {k: len(v) for k, v in graph.items()} == EXPECTED
    assert sum(EXPECTED.values()) == len(rows) == 36
    assert Counter(len(c) for comps in graph.values() for c in comps) == Counter({2: 36})
    assert len(rings) == EXPECTED_RINGS and {len(r) for r in rings} == {2}
