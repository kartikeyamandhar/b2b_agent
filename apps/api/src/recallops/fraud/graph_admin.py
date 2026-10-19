"""Fraud graph administration: schema, the GraphMeta epoch, and retention purges.

Jobs only (worker startup, seed, `python -m recallops.fraud`, tests). API modules never import this module (a
test enforces it), so no request path can purge or rewrite graph metadata. Each function accepts the Neo4j store or
the in-memory store used by unit tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from neo4j import ManagedTransaction

from recallops.fraud.cypher import (
    GET_EPOCH,
    GRAPH_PROGRAMS,
    PURGE_ABSENT_CLAIMS,
    PURGE_FIRM_CLAIMS,
    PURGE_FIRM_IDENTITIES,
    PURGE_NAMESPACE,
    PURGE_PROGRAM_CLAIMS,
    PURGE_PROGRAM_IDENTITIES,
    PURGE_PROGRAM_ORPHANS,
    SCHEMA_STATEMENTS,
    SET_EPOCH,
)
from recallops.fraud.graph import GraphStore, InMemoryGraphStore, Neo4jGraphStore, check_namespace
from recallops.logs import get_logger, log_event

log = get_logger("fraud.graph_admin")


def _neo4j(store: GraphStore) -> Neo4jGraphStore:
    if not isinstance(store, Neo4jGraphStore):
        raise TypeError(f"unsupported graph store {type(store).__name__}")
    return store


def ensure_schema(store: GraphStore) -> None:
    """Idempotent: uid uniqueness on :Claim, :Identity, :GraphMeta and the scope indexes the purges use. Schema
    commands run one per transaction (Neo4j refuses schema and data changes in one transaction)."""
    if isinstance(store, InMemoryGraphStore):
        return
    neo = _neo4j(store)
    for i in range(len(SCHEMA_STATEMENTS)):

        def work(tx: ManagedTransaction, i: int = i) -> None:
            tx.run(SCHEMA_STATEMENTS[i]).consume()

        neo.run_write("ensure_schema", work)


def get_epoch(store: GraphStore, ns: str) -> str | None:
    ns = check_namespace(ns)
    if isinstance(store, InMemoryGraphStore):
        return store.epochs.get(ns)

    def work(tx: ManagedTransaction) -> str | None:
        record = tx.run(GET_EPOCH, ns=ns).single()
        return None if record is None or record["epoch"] is None else str(record["epoch"])

    return _neo4j(store).run_read("get_epoch", work)


def set_epoch(store: GraphStore, ns: str, epoch: str) -> None:
    ns = check_namespace(ns)
    if isinstance(store, InMemoryGraphStore):
        store.epochs[ns] = epoch
        return

    def work(tx: ManagedTransaction) -> None:
        tx.run(SET_EPOCH, ns=ns, epoch=epoch).consume()

    _neo4j(store).run_write("set_epoch", work)


def purge_program(store: GraphStore, ns: str, firm_id: UUID, program_id: UUID) -> int:
    """Delete the program's claims and identities in this namespace. Returns the number of nodes deleted."""
    ns = check_namespace(ns)
    scope = {"ns": ns, "firm_id": str(firm_id), "program_id": str(program_id)}
    if isinstance(store, InMemoryGraphStore):
        doomed = [u for u, n in store.nodes.items() if all(n.props.get(k) == v for k, v in scope.items())]
        for uid in doomed:
            store.delete_node(uid)
        return len(doomed)

    def work(tx: ManagedTransaction) -> int:
        a = tx.run(PURGE_PROGRAM_CLAIMS, scope).single()
        b = tx.run(PURGE_PROGRAM_IDENTITIES, scope).single()
        return sum(int(r["n"]) for r in (a, b) if r is not None)

    n = _neo4j(store).run_write("purge_program", work)
    log_event(log, "graph_purge_program", firm_id=str(firm_id), program_id=str(program_id), nodes=n)
    return n


def purge_firm(store: GraphStore, ns: str, firm_id: UUID) -> int:
    ns = check_namespace(ns)
    if isinstance(store, InMemoryGraphStore):
        doomed = [
            u for u, n in store.nodes.items() if n.props.get("ns") == ns and n.props.get("firm_id") == str(firm_id)
        ]
        for uid in doomed:
            store.delete_node(uid)
        return len(doomed)

    def work(tx: ManagedTransaction) -> int:
        a = tx.run(PURGE_FIRM_CLAIMS, ns=ns, firm_id=str(firm_id)).single()
        b = tx.run(PURGE_FIRM_IDENTITIES, ns=ns, firm_id=str(firm_id)).single()
        return sum(int(r["n"]) for r in (a, b) if r is not None)

    n = _neo4j(store).run_write("purge_firm", work)
    log_event(log, "graph_purge_firm", firm_id=str(firm_id), nodes=n)
    return n


def purge_absent(store: GraphStore, ns: str, firm_id: UUID, program_id: UUID, keep_claim_ids: Iterable[UUID]) -> int:
    """Retention: delete claim nodes of the program that Postgres no longer has (or that are drafts), then any
    identity of the program left without a relationship. Returns the number of claim nodes deleted."""
    ns = check_namespace(ns)
    keep = sorted({str(c) for c in keep_claim_ids})
    scope = {"ns": ns, "firm_id": str(firm_id), "program_id": str(program_id)}
    if isinstance(store, InMemoryGraphStore):
        doomed = [
            u
            for u, n in store.nodes.items()
            if n.label == "Claim"
            and all(n.props.get(k) == v for k, v in scope.items())
            and n.props.get("claim_id") not in keep
        ]
        for uid in doomed:
            store.delete_node(uid)
        orphans = [
            u
            for u, n in store.nodes.items()
            if n.label == "Identity" and all(n.props.get(k) == v for k, v in scope.items()) and store.degree(u) == 0
        ]
        for uid in orphans:
            store.delete_node(uid)
        return len(doomed)

    def work(tx: ManagedTransaction) -> int:
        record = tx.run(PURGE_ABSENT_CLAIMS, scope, keep=keep).single()
        tx.run(PURGE_PROGRAM_ORPHANS, scope).consume()
        return int(record["n"]) if record is not None else 0

    n = _neo4j(store).run_write("purge_absent", work)
    if n:
        log_event(log, "graph_purge_absent", firm_id=str(firm_id), program_id=str(program_id), claims=n)
    return n


def purge_namespace(store: GraphStore, ns: str) -> int:
    """Delete every node of the namespace, GraphMeta included (tests, and a deliberate full rebuild)."""
    ns = check_namespace(ns)
    if isinstance(store, InMemoryGraphStore):
        doomed = [u for u, n in store.nodes.items() if n.props.get("ns") == ns]
        for uid in doomed:
            store.delete_node(uid)
        store.epochs.pop(ns, None)
        return len(doomed)

    def work(tx: ManagedTransaction) -> int:
        record = tx.run(PURGE_NAMESPACE, ns=ns).single()
        return int(record["n"]) if record is not None else 0

    return _neo4j(store).run_write("purge_namespace", work)


def graph_programs(store: GraphStore, ns: str) -> set[tuple[UUID, UUID]]:
    """(firm_id, program_id) pairs present in the namespace, for purging programs Postgres no longer has."""
    ns = check_namespace(ns)
    raw: set[tuple[object, object]]
    if isinstance(store, InMemoryGraphStore):
        raw = {
            (n.props.get("firm_id"), n.props.get("program_id")) for n in store.nodes.values() if n.props.get("ns") == ns
        }
    else:

        def work(tx: ManagedTransaction) -> set[tuple[object, object]]:
            return {(r["firm_id"], r["program_id"]) for r in tx.run(GRAPH_PROGRAMS, ns=ns)}

        raw = _neo4j(store).run_read("graph_programs", work)
    out: set[tuple[UUID, UUID]] = set()
    for firm, program in raw:
        try:
            out.add((UUID(str(firm)), UUID(str(program))))
        except ValueError:
            log_event(log, "graph_bad_scope", firm_id=str(firm)[:80], program_id=str(program)[:80])
    return out
