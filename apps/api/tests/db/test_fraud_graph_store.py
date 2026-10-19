"""The Neo4j store against the docker compose Neo4j: plans, tenancy, read-only sessions, the circuit breaker."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from neo4j import ManagedTransaction

from recallops.claims.tools import ClaimFacts, PhotoRef
from recallops.fraud.cypher import COMPONENT, GRAPH_KINDS, MERGE_CLAIMS, PROGRAM_COMPONENTS, PROGRAM_RINGS, RING
from recallops.fraud.graph import (
    ClaimProjection,
    GraphError,
    GraphUnavailable,
    Neo4jGraphStore,
    graph_status,
    reset_breakers,
)
from recallops.fraud.graph_admin import ensure_schema, get_epoch, graph_programs, purge_namespace, set_epoch
from recallops.settings import Settings, get_settings

pytestmark = [pytest.mark.db, pytest.mark.graph]

FIRM_A, FIRM_B = uuid.UUID(int=0xA1), uuid.UUID(int=0xB1)
PROG_A, PROG_B = uuid.UUID(int=0xA2), uuid.UUID(int=0xB2)


def _ns() -> tuple[str, str]:
    ns = get_settings().neo4j_namespace
    return ns, f"{ns}_b"


def same_facts(claim_id: uuid.UUID) -> ClaimFacts:
    """Identical identities for every claim: photo bytes, address, email, serial, payout destination."""
    return ClaimFacts(
        claim_id=claim_id,
        email="same@example.org",
        address="1 Same St Town CA",
        serial_claimed="SAME-1",
        payout_destination="synth_dest_same",
        photos=(PhotoRef(kind="label", sha256="cd" * 32, phash="00ff00ff00ff00ff"),),
    )


def project_pair(store: Neo4jGraphStore, ns: str, firm: uuid.UUID, program: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    a, b = uuid.uuid4(), uuid.uuid4()
    store.project_claims(
        ns, firm, program, [ClaimProjection(same_facts(a), "1 Mail Rd"), ClaimProjection(same_facts(b), "1 Mail Rd")]
    )
    return a, b


def test_traversals_keep_bfs_pruning(graph_store: Neo4jGraphStore) -> None:
    ns, _ = _ns()
    project_pair(graph_store, ns, FIRM_A, PROG_A)
    params = {"ns": ns, "firm_id": str(FIRM_A), "program_id": str(PROG_A), "claim_id": str(uuid.uuid4())}

    def plan(stmt: str) -> str:
        def work(tx: ManagedTransaction) -> str:
            return json.dumps(tx.run("EXPLAIN " + stmt, params).consume().plan)

        return graph_store.run_read("explain", work)

    statements = [*COMPONENT.values(), RING, *PROGRAM_COMPONENTS.values(), PROGRAM_RINGS]
    for stmt in statements:
        assert "VarLengthExpand(Pruning,BFS" in plan(stmt), stmt
    assert "NodeUniqueIndexSeek" in plan(COMPONENT["same_serial"])


def test_firms_and_namespaces_never_share_a_node_or_relationship(graph_store: Neo4jGraphStore) -> None:
    ns, ns_b = _ns()
    a = project_pair(graph_store, ns, FIRM_A, PROG_A)
    b = project_pair(graph_store, ns, FIRM_B, PROG_B)
    c = project_pair(graph_store, ns_b, FIRM_A, PROG_A)  # same firm and program, another namespace

    def work(tx: ManagedTransaction) -> tuple[int, int, int]:
        cross = tx.run(
            "MATCH (x)-[r]-(y) WHERE x.ns IN $nss AND (x.ns <> y.ns OR x.firm_id <> y.firm_id "
            "OR x.program_id <> y.program_id OR r.ns <> x.ns OR r.firm_id <> x.firm_id) RETURN count(r) AS n",
            nss=[ns, ns_b],
        ).single()
        nodes = tx.run("MATCH (n) WHERE n.ns IN $nss RETURN count(n) AS n", nss=[ns, ns_b]).single()
        keys = tx.run(
            "MATCH (i:Identity) WHERE i.ns IN $nss RETURN count(DISTINCT i.key) AS n", nss=[ns, ns_b]
        ).single()
        assert cross is not None and nodes is not None and keys is not None
        return int(cross["n"]), int(nodes["n"]), int(keys["n"])

    cross, nodes, keys = graph_store.run_read("inspect", work)
    assert cross == 0
    # Per scope: 2 claims + 6 identities (photo_label, address, email, serial, payout, mailing) = 8 nodes; 3 scopes.
    assert nodes == 24
    assert keys == 12  # firm A's keys repeat across namespaces (same firm, same HMAC), firm B's differ
    for kind in GRAPH_KINDS:
        assert graph_store.components(ns, FIRM_A, PROG_A, a[0], kind) == set(a)
        assert graph_store.components(ns, FIRM_B, PROG_B, b[0], kind) == set(b)
        assert graph_store.components(ns_b, FIRM_A, PROG_A, c[0], kind) == set(c)
        assert graph_store.program_components(ns, FIRM_A, PROG_A, kind) == [frozenset(a)]
    assert graph_store.ring(ns, FIRM_B, PROG_B, b[1]) == set(b)
    assert graph_programs(graph_store, ns) == {(FIRM_A, PROG_A), (FIRM_B, PROG_B)}


def test_planted_cross_firm_relationships_do_not_join_components(graph_store: Neo4jGraphStore) -> None:
    ns, _ = _ns()
    a = project_pair(graph_store, ns, FIRM_A, PROG_A)
    b = project_pair(graph_store, ns, FIRM_B, PROG_B)
    scope_a: dict[str, Any] = {"ns": ns, "firm_id": str(FIRM_A), "program_id": str(PROG_A)}

    def plant(tx: ManagedTransaction) -> None:
        ua, ub = f"{ns}:{FIRM_A}:{PROG_A}:{a[0]}", f"{ns}:{FIRM_B}:{PROG_B}:{b[0]}"
        tx.run(
            "MATCH (x:Claim {uid: $ua}), (y:Claim {uid: $ub}) "
            "CREATE (x)-[:SAME_SERIAL]->(y) "  # no scope at all
            "CREATE (x)-[:SAME_SERIAL {ns: $ns, firm_id: $firm_id, program_id: $program_id}]->(y) "  # A's scope
            "CREATE (x)-[:SAME_PHOTO {ns: $ns, firm_id: $firm_id, program_id: $program_id}]->(y)",
            scope_a,
            ua=ua,
            ub=ub,
        ).consume()
        tx.run(  # and firm A's scope from firm A's claim to firm B's serial identity
            "MATCH (x:Claim {uid: $ua}), (i:Identity) WHERE i.ns = $ns AND i.firm_id = $fb AND i.kind = 'serial' "
            "CREATE (x)-[:SAME_SERIAL {ns: $ns, firm_id: $firm_id, program_id: $program_id}]->(i)",
            scope_a,
            ua=ua,
            fb=str(FIRM_B),
        ).consume()

    graph_store.run_write("plant", plant)
    for kind in ("same_serial", "same_photo"):
        assert graph_store.components(ns, FIRM_A, PROG_A, a[0], kind) == set(a)
        assert graph_store.components(ns, FIRM_B, PROG_B, b[0], kind) == set(b)
        assert graph_store.program_components(ns, FIRM_A, PROG_A, kind) == [frozenset(a)]
        assert graph_store.program_components(ns, FIRM_B, PROG_B, kind) == [frozenset(b)]
    assert graph_store.ring(ns, FIRM_A, PROG_A, a[1]) == set(a)
    assert graph_store.program_rings(ns, FIRM_B, PROG_B) == [frozenset(b)]
    edges = graph_store.program_edges(ns, FIRM_A, PROG_A, [*a, *b], 100)
    assert {x for e in edges for x in (e.a, e.b)} == set(a)
    # Re-projecting the claim deletes every relationship it has, the planted ones included.
    graph_store.project_claims(ns, FIRM_A, PROG_A, [ClaimProjection(same_facts(a[0]), "1 Mail Rd")])

    def planted(tx: ManagedTransaction) -> int:
        record = tx.run(
            "MATCH (x:Claim)-[r]-(y:Claim) WHERE x.ns = $ns AND x.firm_id <> y.firm_id RETURN count(r) AS n", ns=ns
        ).single()
        return int(record["n"]) if record else -1

    assert graph_store.run_read("planted", planted) == 0


def test_read_sessions_refuse_writes(graph_store: Neo4jGraphStore) -> None:
    ns, _ = _ns()

    def write(tx: ManagedTransaction) -> None:
        tx.run(MERGE_CLAIMS, ns=ns, firm_id=str(FIRM_A), program_id=str(PROG_A), claim_ids=["x"]).consume()

    with pytest.raises(GraphError):
        graph_store.run_read("write_in_read", write)
    assert graph_store.components(ns, FIRM_A, PROG_A, uuid.UUID(int=1), "same_serial") == {uuid.UUID(int=1)}


def test_schema_and_epoch(graph_store: Neo4jGraphStore) -> None:
    ns, _ = _ns()
    ensure_schema(graph_store)  # idempotent
    assert get_epoch(graph_store, ns) is None
    set_epoch(graph_store, ns, "epoch-1")
    assert get_epoch(graph_store, ns) == "epoch-1"
    assert purge_namespace(graph_store, ns) == 1
    assert get_epoch(graph_store, ns) is None
    assert graph_status(graph_store) == "ok"


@pytest.fixture()
def unreachable(monkeypatch: pytest.MonkeyPatch) -> Iterator[Neo4jGraphStore]:
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    monkeypatch.setenv("NEO4J_TIMEOUT_S", "1")
    reset_breakers()
    store = Neo4jGraphStore(Settings())
    yield store
    store.close()
    reset_breakers()


def test_circuit_breaker_fails_fast_after_the_first_failure(unreachable: Neo4jGraphStore) -> None:
    assert unreachable.timeout_s == 1.0
    ns, _ = _ns()
    t0 = time.monotonic()
    with pytest.raises(GraphUnavailable):
        unreachable.components(ns, FIRM_A, PROG_A, uuid.UUID(int=1), "same_serial")
    first = time.monotonic() - t0
    assert first < 2 * unreachable.timeout_s
    t1 = time.monotonic()
    with pytest.raises(GraphUnavailable, match="circuit open"):
        unreachable.project_claims(ns, FIRM_A, PROG_A, [ClaimProjection(same_facts(uuid.UUID(int=2)))])
    assert time.monotonic() - t1 < 0.05
    t2 = time.monotonic()
    assert unreachable.status() == "unavailable"
    assert time.monotonic() - t2 < 0.05
    reset_breakers()
    t3 = time.monotonic()
    assert unreachable.status() == "unavailable"  # a real probe, bounded by the connection timeout
    assert time.monotonic() - t3 < unreachable.timeout_s + 0.5
