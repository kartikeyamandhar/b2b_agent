"""The fraud graph store: a Neo4j projection of Postgres claims (plan WP1, Revision 1; decisions log 2026-10-18).

Postgres stays the system of record. The graph holds, per namespace, firm, and program, one :Claim node per non-draft
claim and one :Identity node per normalized identity value, linked by one relationship type per duplicate kind:

    same_photo    (Claim)-[:SAME_PHOTO]->(Identity photo_<photo kind>, keyed by the photo sha256)
                  (Claim)-[:SIMILAR_PHOTO {photo_kind, bits}]-(Claim), pHash within the duplicate threshold,
                  same photo kind, label/destruction/other only (receipts match on exact bytes only)
    same_address  (Claim)-[:SAME_ADDRESS]->(Identity address | email)
    same_serial   (Claim)-[:SAME_SERIAL]->(Identity serial)
    same_payout   (Claim)-[:SAME_PAYOUT]->(Identity payout): the claimant's payout_destination, compared as the exact
                  string, as check_duplicates does. Only the synthetic generators set it today.
    same_mailing  (Claim)-[:SAME_MAILING]->(Identity mailing): claimant.mailing_address, the post-approval payout
                  address from the portal. Not part of check_duplicates.

Identity values never reach the graph raw: key = HMAC-SHA256(k_firm, identity_kind + "|" + normalized value), with
k_firm = HMAC-SHA256(GRAPH_KEY_SECRET, firm_id), so equal values in two firms get unrelated keys. Normalizers are the
ones check_duplicates uses (_norm_text, norm_code); a value that normalizes to "" creates no identity (check_duplicates
would still match an empty serial against a missing one; the graph deliberately does not).

Every statement is a constant in fraud/cypher.py. Reads run in READ_ACCESS sessions. Driver failures become
GraphUnavailable and open a per-process circuit breaker for GRAPH_BACKOFF_S, during which calls fail fast.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from neo4j import READ_ACCESS, WRITE_ACCESS, Driver, GraphDatabase, ManagedTransaction, unit_of_work
from neo4j.exceptions import (
    AuthError,
    ConnectionPoolError,
    DriverError,
    Forbidden,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from recallops.claims.tools import PHASH_KINDS, ClaimFacts
from recallops.claims.tools.checks import _norm_text, norm_code
from recallops.fraud.cypher import (
    COMPONENT,
    DELETE_CLAIM_RELS,
    DELETE_ORPHAN_IDENTITIES,
    GRAPH_KINDS,
    IDENTITY_REL,
    LINK_IDENTITY,
    LINK_SIMILAR,
    MERGE_CLAIMS,
    PAIRS_VIA_IDENTITY,
    PAIRS_VIA_SIMILAR,
    PROGRAM_COMPONENTS,
    PROGRAM_RINGS,
    REL_KIND,
    RING,
    RING_REL_TYPES,
    GraphKind,
)
from recallops.logs import get_logger, log_event
from recallops.settings import Settings

log = get_logger("fraud.graph")

GraphStatus = Literal["ok", "disabled", "unavailable"]
NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
T = TypeVar("T")


class GraphError(Exception):
    """A graph operation failed for a reason other than availability (a query or data error)."""


class GraphUnavailable(GraphError):
    """Neo4j cannot be reached, refused the credentials, or timed out; or the circuit breaker is open."""


class SimilarPhoto(NamedTuple):
    other_claim_id: UUID
    photo_kind: str
    bits: int


class GraphEdge(NamedTuple):
    a: UUID
    b: UUID
    kind: GraphKind


class IdentityLink(NamedTuple):
    kind: GraphKind
    identity_kind: str
    key: str


@dataclass(frozen=True)
class ClaimProjection:
    """What the graph stores for one claim: its facts (normalized and keyed here), mailing address, pHash peers."""

    facts: ClaimFacts
    mailing_address: str | None = None
    similar: tuple[SimilarPhoto, ...] = ()


# ---------------------------------------------------------------------------------------------------------------
# Identity keys


def firm_key(secret: bytes, firm_id: UUID) -> bytes:
    return hmac.new(secret, str(firm_id).encode("utf-8"), hashlib.sha256).digest()


def identity_key(k_firm: bytes, identity_kind: str, value: str) -> str:
    return hmac.new(k_firm, f"{identity_kind}|{value}".encode(), hashlib.sha256).hexdigest()


def identity_links(
    secret: bytes, firm_id: UUID, facts: ClaimFacts, mailing_address: str | None = None
) -> list[IdentityLink]:
    """The claim's identities, sorted. Photo identities are scoped by photo kind, as check_duplicates compares
    photos of the same kind only; values that normalize to an empty string create no identity."""
    k_firm = firm_key(secret, firm_id)
    raw: list[tuple[GraphKind, str, str]] = [("same_photo", f"photo_{p.kind}", p.sha256) for p in facts.photos]
    raw += [
        ("same_address", "address", _norm_text(facts.address)),
        ("same_address", "email", _norm_text(facts.email)),
        ("same_serial", "serial", norm_code(facts.serial_claimed)),
        ("same_payout", "payout", facts.payout_destination or ""),
        ("same_mailing", "mailing", _norm_text(mailing_address)),
    ]
    links = {IdentityLink(kind, ik, identity_key(k_firm, ik, value)) for kind, ik, value in raw if value}
    return sorted(links)


def check_namespace(ns: str) -> str:
    if not NAMESPACE_RE.fullmatch(ns):
        raise ValueError(f"invalid graph namespace {ns!r}")
    return ns


def _scope(ns: str, firm_id: UUID, program_id: UUID) -> dict[str, Any]:
    if not isinstance(firm_id, UUID) or not isinstance(program_id, UUID):
        raise TypeError("firm_id and program_id must be UUIDs")
    return {"ns": check_namespace(ns), "firm_id": str(firm_id), "program_id": str(program_id)}


def _check_kind(kind: str) -> GraphKind:
    for known in GRAPH_KINDS:
        if kind == known:
            return known
    raise ValueError(f"unknown graph kind {kind!r}")


def parse_claim_ids(values: Iterable[object], *, source: str) -> set[UUID]:
    """Graph output is untrusted: anything that is not a UUID string is dropped and logged."""
    out: set[UUID] = set()
    for v in values:
        try:
            out.add(UUID(str(v)))
        except ValueError:
            log_event(log, "graph_bad_claim_id", level=logging.ERROR, source=source, value=str(v)[:80])
    return out


def _groups(rows: Iterable[tuple[object, object]], *, source: str) -> list[frozenset[UUID]]:
    by_root: dict[str, set[object]] = defaultdict(set)
    for claim_id, root in rows:
        by_root[str(root)].add(claim_id)
        by_root[str(root)].add(root)
    comps = [frozenset(parse_claim_ids(members, source=source)) for members in by_root.values()]
    return sorted((c for c in comps if len(c) >= 2), key=lambda c: (-len(c), min(str(x) for x in c)))


# ---------------------------------------------------------------------------------------------------------------
# Protocol


@runtime_checkable
class GraphStore(Protocol):
    def status(self) -> GraphStatus: ...

    def project_claim(
        self,
        ns: str,
        firm_id: UUID,
        program_id: UUID,
        claim_facts: ClaimFacts,
        mailing_address: str | None,
        similar: Sequence[tuple[UUID, str, int]],
    ) -> None: ...

    def project_claims(self, ns: str, firm_id: UUID, program_id: UUID, items: Sequence[ClaimProjection]) -> None: ...

    def components(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID, kind: GraphKind) -> set[UUID]: ...

    def ring(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID) -> set[UUID]: ...

    def program_components(
        self, ns: str, firm_id: UUID, program_id: UUID, kind: GraphKind
    ) -> list[frozenset[UUID]]: ...

    def program_rings(self, ns: str, firm_id: UUID, program_id: UUID) -> list[frozenset[UUID]]: ...

    def program_edges(
        self, ns: str, firm_id: UUID, program_id: UUID, claim_ids: Iterable[UUID], limit: int
    ) -> list[GraphEdge]: ...

    def close(self) -> None: ...


def _similar(similar: Sequence[tuple[UUID, str, int]]) -> tuple[SimilarPhoto, ...]:
    out = []
    for other, photo_kind, bits in similar:
        if photo_kind not in PHASH_KINDS:
            raise ValueError(f"SIMILAR_PHOTO is for {sorted(PHASH_KINDS)} photos only, not {photo_kind!r}")
        out.append(SimilarPhoto(other, photo_kind, int(bits)))
    return tuple(out)


# ---------------------------------------------------------------------------------------------------------------
# Circuit breaker (per process, per URI and database)


class CircuitBreaker:
    def __init__(self, backoff_s: float) -> None:
        self.backoff_s = backoff_s
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._open_until

    def check(self) -> None:
        with self._lock:
            remaining = self._open_until - time.monotonic()
        if remaining > 0:
            raise GraphUnavailable(f"fraud graph circuit open for another {remaining:.1f}s")

    def trip(self) -> None:
        with self._lock:
            self._open_until = time.monotonic() + self.backoff_s

    def reset(self) -> None:
        with self._lock:
            self._open_until = 0.0


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def breaker_for(uri: str, database: str, backoff_s: float) -> CircuitBreaker:
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(f"{uri}|{database}")
        if breaker is None:
            breaker = _BREAKERS[f"{uri}|{database}"] = CircuitBreaker(backoff_s)
        breaker.backoff_s = backoff_s
        return breaker


def reset_breakers() -> None:
    with _BREAKERS_LOCK:
        for breaker in _BREAKERS.values():
            breaker.reset()


UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    ServiceUnavailable,
    SessionExpired,
    ConnectionPoolError,
    AuthError,
    Forbidden,
    TransientError,
    TimeoutError,
    OSError,
)


# ---------------------------------------------------------------------------------------------------------------
# Neo4j


class Neo4jGraphStore:
    """Neo4j-backed store. Timeouts derive from NEO4J_TIMEOUT_S: connection and pool acquisition wait at most the
    timeout, a transaction may run for the timeout server side, and managed retries of transient failures stop
    after half of it, so an unreachable server fails in well under twice the timeout."""

    def __init__(self, settings: Settings, driver: Driver | None = None) -> None:
        if not settings.neo4j_uri:
            raise ValueError("NEO4J_URI is empty: the fraud graph is disabled")
        self.namespace = check_namespace(settings.neo4j_namespace)
        self.uri = settings.neo4j_uri
        self.database = settings.neo4j_database
        self.timeout_s = settings.neo4j_timeout_s
        self.backoff_s = settings.graph_backoff_s
        self._secret = settings.graph_key_secret.get_secret_value().encode("utf-8")
        self._driver = driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            connection_timeout=self.timeout_s,
            connection_acquisition_timeout=self.timeout_s,
            max_transaction_retry_time=self.timeout_s / 2,
            notifications_min_severity="OFF",
            user_agent="recallops-fraud-graph",
        )
        self._breaker = breaker_for(self.uri, self.database, self.backoff_s)
        self._status: tuple[float, GraphStatus] | None = None
        self._status_lock = threading.Lock()

    # -- plumbing ------------------------------------------------------------------------------------------------

    @contextmanager
    def _guard(self, op: str) -> Iterator[None]:
        self._breaker.check()
        t0 = time.monotonic()
        try:
            yield
        except UNAVAILABLE_ERRORS as exc:
            self._breaker.trip()
            log_event(
                log,
                "graph_unavailable",
                level=logging.WARNING,
                op=op,
                error=type(exc).__name__,
                backoff_s=self.backoff_s,
                duration_ms=round((time.monotonic() - t0) * 1000),
            )
            raise GraphUnavailable(f"fraud graph unavailable during {op}: {type(exc).__name__}") from exc
        except (Neo4jError, DriverError) as exc:
            log_event(log, "graph_error", level=logging.ERROR, op=op, error=type(exc).__name__)
            raise GraphError(f"fraud graph error during {op}: {type(exc).__name__}") from exc

    def run_write(self, op: str, work: Callable[[ManagedTransaction], T]) -> T:
        """One explicit write transaction (managed: retried on transient errors within the retry budget)."""
        with (
            self._guard(op),
            self._driver.session(
                database=self.database,
                default_access_mode=WRITE_ACCESS,
                bookmark_manager=self._driver.execute_query_bookmark_manager,
            ) as session,
        ):
            return session.execute_write(unit_of_work(timeout=self.timeout_s)(work))

    def run_read(self, op: str, work: Callable[[ManagedTransaction], T]) -> T:
        """One read transaction in a READ_ACCESS session: the server refuses any write in it."""
        with (
            self._guard(op),
            self._driver.session(
                database=self.database,
                default_access_mode=READ_ACCESS,
                bookmark_manager=self._driver.execute_query_bookmark_manager,
            ) as session,
        ):
            return session.execute_read(unit_of_work(timeout=self.timeout_s)(work))

    def status(self) -> GraphStatus:
        """ok or unavailable, cached for GRAPH_BACKOFF_S; an open breaker answers at once, a probe waits at most the
        connection timeout."""
        if self._breaker.is_open():
            return "unavailable"
        with self._status_lock:
            cached = self._status
        if cached is not None and time.monotonic() - cached[0] < self.backoff_s:
            return cached[1]
        value: GraphStatus = "ok"
        try:
            with self._guard("status"):
                self._driver.verify_connectivity()
        except GraphError:
            value = "unavailable"
        with self._status_lock:
            self._status = (time.monotonic(), value)
        return value

    def close(self) -> None:
        self._driver.close()

    # -- writes --------------------------------------------------------------------------------------------------

    def project_claim(
        self,
        ns: str,
        firm_id: UUID,
        program_id: UUID,
        claim_facts: ClaimFacts,
        mailing_address: str | None,
        similar: Sequence[tuple[UUID, str, int]],
    ) -> None:
        self.project_claims(ns, firm_id, program_id, [ClaimProjection(claim_facts, mailing_address, _similar(similar))])

    def project_claims(self, ns: str, firm_id: UUID, program_id: UUID, items: Sequence[ClaimProjection]) -> None:
        """One write transaction: delete every relationship of these claims (both directions, all types), re-MERGE
        the claim nodes, their identities and edges, then delete identities left without any relationship."""
        if not items:
            return
        params = _scope(ns, firm_id, program_id)
        claim_ids = [str(item.facts.claim_id) for item in items]
        links: dict[GraphKind, list[dict[str, str]]] = {kind: [] for kind in GRAPH_KINDS}
        pairs: list[dict[str, Any]] = []
        for item in items:
            cid = str(item.facts.claim_id)
            for link in identity_links(self._secret, firm_id, item.facts, item.mailing_address):
                links[link.kind].append({"claim_id": cid, "identity_kind": link.identity_kind, "key": link.key})
            for s in _similar(item.similar):
                if s.other_claim_id != item.facts.claim_id:
                    pairs.append(
                        {"claim_id": cid, "other_id": str(s.other_claim_id), "photo_kind": s.photo_kind, "bits": s.bits}
                    )

        def work(tx: ManagedTransaction) -> None:
            stale = [r["identity_uid"] for r in tx.run(DELETE_CLAIM_RELS, params, claim_ids=claim_ids)]
            tx.run(MERGE_CLAIMS, params, claim_ids=claim_ids).consume()
            for kind in GRAPH_KINDS:
                if links[kind]:
                    tx.run(LINK_IDENTITY[kind], params, links=links[kind]).consume()
            if pairs:
                tx.run(LINK_SIMILAR, params, pairs=pairs).consume()
            orphans = sorted({u for u in stale if u})
            if orphans:
                tx.run(DELETE_ORPHAN_IDENTITIES, params, identity_uids=orphans).consume()

        t0 = time.monotonic()
        self.run_write("project_claims", work)
        log_event(
            log,
            "graph_projected",
            firm_id=str(firm_id),
            program_id=str(program_id),
            claims=len(items),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )

    # -- reads ---------------------------------------------------------------------------------------------------

    def components(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID, kind: GraphKind) -> set[UUID]:
        params = {**_scope(ns, firm_id, program_id), "claim_id": str(claim_id)}
        kind = _check_kind(kind)

        def work(tx: ManagedTransaction) -> list[object]:
            return [r["claim_id"] for r in tx.run(COMPONENT[kind], params)]

        return {claim_id} | parse_claim_ids(self.run_read("components", work), source="components")

    def ring(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID) -> set[UUID]:
        params = {**_scope(ns, firm_id, program_id), "claim_id": str(claim_id)}

        def work(tx: ManagedTransaction) -> list[object]:
            return [r["claim_id"] for r in tx.run(RING, params)]

        return {claim_id} | parse_claim_ids(self.run_read("ring", work), source="ring")

    def program_components(self, ns: str, firm_id: UUID, program_id: UUID, kind: GraphKind) -> list[frozenset[UUID]]:
        params = _scope(ns, firm_id, program_id)
        kind = _check_kind(kind)

        def work(tx: ManagedTransaction) -> list[tuple[object, object]]:
            return [(r["claim_id"], r["root"]) for r in tx.run(PROGRAM_COMPONENTS[kind], params)]

        return _groups(self.run_read("program_components", work), source="program_components")

    def program_rings(self, ns: str, firm_id: UUID, program_id: UUID) -> list[frozenset[UUID]]:
        params = _scope(ns, firm_id, program_id)

        def work(tx: ManagedTransaction) -> list[tuple[object, object]]:
            return [(r["claim_id"], r["root"]) for r in tx.run(PROGRAM_RINGS, params)]

        return _groups(self.run_read("program_rings", work), source="program_rings")

    def program_edges(
        self, ns: str, firm_id: UUID, program_id: UUID, claim_ids: Iterable[UUID], limit: int
    ) -> list[GraphEdge]:
        params = _scope(ns, firm_id, program_id)
        ids = sorted({str(c) for c in claim_ids})
        if not ids or limit <= 0:
            return []

        def work(tx: ManagedTransaction) -> list[tuple[object, object, object]]:
            rows = [(r["a"], r["b"], r["rel"]) for r in tx.run(PAIRS_VIA_IDENTITY, params, claim_ids=ids, limit=limit)]
            rows += [(r["a"], r["b"], r["rel"]) for r in tx.run(PAIRS_VIA_SIMILAR, params, claim_ids=ids, limit=limit)]
            return rows

        return _edges(self.run_read("program_edges", work), limit)


def _edges(rows: Iterable[tuple[object, object, object]], limit: int) -> list[GraphEdge]:
    out: set[GraphEdge] = set()
    for a, b, rel in rows:
        kind = REL_KIND.get(str(rel))
        parsed = sorted(parse_claim_ids([a, b], source="program_edges"), key=str)
        if kind is None or len(parsed) != 2:
            continue
        out.add(GraphEdge(parsed[0], parsed[1], kind))
    return sorted(out, key=lambda e: (str(e.a), str(e.b), e.kind))[:limit]


# ---------------------------------------------------------------------------------------------------------------
# In memory (unit tests): the same semantics as the Neo4j store, in plain Python.


@dataclass
class _Node:
    label: Literal["Claim", "Identity"]
    props: dict[str, str]


@dataclass
class _Rel:
    type: str
    a: str
    b: str
    props: dict[str, Any] = field(default_factory=dict)


class InMemoryGraphStore:
    """Pure-Python store with the Neo4j store's semantics: scoped uids, per-hop scope checks, endpoint filters,
    relationship deletion and re-merge on projection, orphan identity cleanup. test helpers: plant()."""

    def __init__(self, secret: bytes = b"in-memory-graph-key-secret-32-bytes!") -> None:
        self._secret = secret
        self.nodes: dict[str, _Node] = {}
        self.rels: dict[int, _Rel] = {}
        self._adj: dict[str, set[int]] = defaultdict(set)
        self._next = 0
        self.epochs: dict[str, str] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> InMemoryGraphStore:
        return cls(settings.graph_key_secret.get_secret_value().encode("utf-8"))

    def status(self) -> GraphStatus:
        return "ok"

    def close(self) -> None:
        return None

    # -- helpers -------------------------------------------------------------------------------------------------

    @staticmethod
    def claim_uid(ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID | str) -> str:
        return f"{ns}:{firm_id}:{program_id}:{claim_id}"

    def _add_rel(self, rel: _Rel) -> None:
        self._next += 1
        self.rels[self._next] = rel
        self._adj[rel.a].add(self._next)
        self._adj[rel.b].add(self._next)

    def _del_rel(self, rid: int) -> None:
        rel = self.rels.pop(rid)
        self._adj[rel.a].discard(rid)
        self._adj[rel.b].discard(rid)

    def degree(self, uid: str) -> int:
        return len(self._adj.get(uid, ()))

    def delete_node(self, uid: str) -> None:
        for rid in list(self._adj.get(uid, ())):
            self._del_rel(rid)
        self.nodes.pop(uid, None)
        self._adj.pop(uid, None)

    def plant(self, rel_type: str, a_uid: str, b_uid: str, props: dict[str, Any] | None = None) -> None:
        """Test helper: add a relationship by hand, as an attacker or a bug could."""
        self._add_rel(_Rel(rel_type, a_uid, b_uid, dict(props or {})))

    @staticmethod
    def _in_scope(props: dict[str, Any], scope: dict[str, Any]) -> bool:
        return all(props.get(k) == v for k, v in scope.items())

    # -- writes --------------------------------------------------------------------------------------------------

    def project_claim(
        self,
        ns: str,
        firm_id: UUID,
        program_id: UUID,
        claim_facts: ClaimFacts,
        mailing_address: str | None,
        similar: Sequence[tuple[UUID, str, int]],
    ) -> None:
        self.project_claims(ns, firm_id, program_id, [ClaimProjection(claim_facts, mailing_address, _similar(similar))])

    def project_claims(self, ns: str, firm_id: UUID, program_id: UUID, items: Sequence[ClaimProjection]) -> None:
        scope = _scope(ns, firm_id, program_id)
        stale: set[str] = set()
        for item in items:
            uid = self.claim_uid(ns, firm_id, program_id, item.facts.claim_id)
            for rid in list(self._adj.get(uid, ())):
                rel = self.rels[rid]
                other = rel.b if rel.a == uid else rel.a
                if other in self.nodes and self.nodes[other].label == "Identity":
                    stale.add(other)
                self._del_rel(rid)
        for item in items:
            uid = self.claim_uid(ns, firm_id, program_id, item.facts.claim_id)
            self.nodes[uid] = _Node("Claim", {**scope, "claim_id": str(item.facts.claim_id)})
        for item in items:
            uid = self.claim_uid(ns, firm_id, program_id, item.facts.claim_id)
            for link in identity_links(self._secret, firm_id, item.facts, item.mailing_address):
                iuid = f"{ns}:{firm_id}:{program_id}:{link.identity_kind}:{link.key}"
                self.nodes[iuid] = _Node("Identity", {**scope, "kind": link.identity_kind, "key": link.key})
                rel_type = IDENTITY_REL[link.kind]
                exists = any(
                    self.rels[r].type == rel_type and self.rels[r].a == uid and self.rels[r].b == iuid
                    for r in self._adj[uid]
                )
                if not exists:
                    self._add_rel(_Rel(rel_type, uid, iuid, dict(scope)))
            for s in _similar(item.similar):
                ouid = self.claim_uid(ns, firm_id, program_id, s.other_claim_id)
                peer = self.nodes.get(ouid)
                if ouid == uid or peer is None or peer.label != "Claim" or not self._in_scope(peer.props, scope):
                    continue
                props = {**scope, "photo_kind": s.photo_kind}
                found = [
                    r
                    for r in self._adj[uid]
                    if self.rels[r].type == "SIMILAR_PHOTO"
                    and {self.rels[r].a, self.rels[r].b} == {uid, ouid}
                    and all(self.rels[r].props.get(k) == v for k, v in props.items())
                ]
                if found:
                    self.rels[found[0]].props["bits"] = s.bits
                else:
                    self._add_rel(_Rel("SIMILAR_PHOTO", uid, ouid, {**props, "bits": s.bits}))
        for iuid in stale:
            node = self.nodes.get(iuid)
            if node is not None and self._in_scope(node.props, scope) and not self._adj.get(iuid):
                self.delete_node(iuid)

    # -- reads ---------------------------------------------------------------------------------------------------

    def _reach(self, start: str, types: Sequence[str], scope: dict[str, Any]) -> set[UUID]:
        if start not in self.nodes:
            return set()
        seen = {start}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for rid in self._adj.get(node, ()):
                rel = self.rels[rid]
                if rel.type not in types or not self._in_scope(rel.props, scope):
                    continue
                other = rel.b if rel.a == node else rel.a
                if other not in seen and other in self.nodes:
                    seen.add(other)
                    queue.append(other)
        seen.discard(start)
        ids = [
            self.nodes[n].props.get("claim_id")
            for n in seen
            if self.nodes[n].label == "Claim" and self._in_scope(self.nodes[n].props, scope)
        ]
        return parse_claim_ids(ids, source="in_memory")

    def components(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID, kind: GraphKind) -> set[UUID]:
        scope = _scope(ns, firm_id, program_id)
        types = [t for t, k in REL_KIND.items() if k == _check_kind(kind)]
        return {claim_id} | self._reach(self.claim_uid(ns, firm_id, program_id, claim_id), types, scope)

    def ring(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID) -> set[UUID]:
        scope = _scope(ns, firm_id, program_id)
        return {claim_id} | self._reach(self.claim_uid(ns, firm_id, program_id, claim_id), RING_REL_TYPES, scope)

    def _program_claims(self, scope: dict[str, Any]) -> list[str]:
        return sorted(u for u, n in self.nodes.items() if n.label == "Claim" and self._in_scope(n.props, scope))

    def _program_groups(self, ns: str, firm_id: UUID, program_id: UUID, types: Sequence[str]) -> list[frozenset[UUID]]:
        scope = _scope(ns, firm_id, program_id)
        rows: list[tuple[object, object]] = []
        for uid in self._program_claims(scope):
            cid = self.nodes[uid].props["claim_id"]
            reached = self._reach(uid, types, scope)
            if reached:
                rows.append((cid, min([cid, *(str(r) for r in reached)])))
        return _groups(rows, source="in_memory")

    def program_components(self, ns: str, firm_id: UUID, program_id: UUID, kind: GraphKind) -> list[frozenset[UUID]]:
        types = [t for t, k in REL_KIND.items() if k == _check_kind(kind)]
        return self._program_groups(ns, firm_id, program_id, types)

    def program_rings(self, ns: str, firm_id: UUID, program_id: UUID) -> list[frozenset[UUID]]:
        return self._program_groups(ns, firm_id, program_id, RING_REL_TYPES)

    def program_edges(
        self, ns: str, firm_id: UUID, program_id: UUID, claim_ids: Iterable[UUID], limit: int
    ) -> list[GraphEdge]:
        scope = _scope(ns, firm_id, program_id)
        wanted = {self.claim_uid(ns, firm_id, program_id, c) for c in claim_ids}
        rows: list[tuple[object, object, object]] = []
        for uid in wanted:
            node = self.nodes.get(uid)
            if node is None or node.label != "Claim":
                continue
            for rid in self._adj.get(uid, ()):
                rel = self.rels[rid]
                if rel.type not in REL_KIND or not self._in_scope(rel.props, scope):
                    continue
                other = rel.b if rel.a == uid else rel.a
                onode = self.nodes.get(other)
                if onode is None:
                    continue
                if rel.type == "SIMILAR_PHOTO":
                    if other in wanted and onode.label == "Claim" and self._in_scope(onode.props, scope):
                        rows.append((node.props["claim_id"], onode.props["claim_id"], rel.type))
                    continue
                if rel.a != uid or onode.label != "Identity" or not self._in_scope(onode.props, scope):
                    continue
                for rid2 in self._adj.get(other, ()):
                    rel2 = self.rels[rid2]
                    peer = rel2.a
                    pnode = self.nodes.get(peer)
                    if (
                        rel2.type == rel.type
                        and rel2.b == other
                        and peer != uid
                        and peer in wanted
                        and pnode is not None
                        and pnode.label == "Claim"
                        and self._in_scope(rel2.props, scope)
                        and self._in_scope(pnode.props, scope)
                    ):
                        rows.append((node.props["claim_id"], pnode.props["claim_id"], rel.type))
        return _edges(rows, limit)


def build_graph_store(settings: Settings) -> Neo4jGraphStore | None:
    """None when NEO4J_URI is empty (graph disabled). Building connects to nothing; the first call does."""
    if not settings.neo4j_uri:
        return None
    return Neo4jGraphStore(settings)


def graph_status(store: GraphStore | None) -> GraphStatus:
    return "disabled" if store is None else store.status()
