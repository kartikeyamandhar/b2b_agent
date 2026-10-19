"""Projection: Postgres claims into the fraud graph, and graph components back into fraud_clusters.

This module never changes a claim's status or a payout. It reads claims, writes graph nodes and edges, rewrites
fraud_clusters rows, and records claims.graph_synced_version. Money safety (reopening duplicates) lives in Postgres
code paths that use the frozen check_duplicates tool, not here.

Order inside sync_claim (one Postgres transaction on the app role, RLS enforced, per-program advisory lock held):

    1. lock the program; load the claim (skip drafts), its graph_version, its facts, and the program population
    2. project the claim into Neo4j (one write transaction: delete its relationships, re-merge)
    3. read the claim's component per kind from Neo4j (READ_ACCESS)
    4. intersect every returned id with the firm's non-draft claims of the program (graph output is untrusted)
    5. rewrite fraud_clusters rows of each kind whose members meet the component (merges keep the oldest row id)
    6. fence: graph_synced_version = the version read, only if graph_version has not moved since
    7. commit (a GraphUnavailable before this point rolls everything back and propagates)
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import batched
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from recallops.claims.context import facts_from_rows, program_claims
from recallops.claims.tools import PHASH_KINDS, ClaimFacts
from recallops.db.session import tenant_session
from recallops.fraud.cypher import GRAPH_KINDS, RING_REL_TYPES, GraphKind
from recallops.fraud.graph import ClaimProjection, GraphStore, GraphUnavailable, SimilarPhoto
from recallops.fraud.graph_admin import get_epoch, purge_absent, set_epoch
from recallops.fraud.phash import hamming
from recallops.logs import get_logger, log_event
from recallops.models import Claim, ClaimPhoto
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("fraud.sync")

SYNC_LOCK_CLASS = 7_420_018  # pg_advisory_xact_lock(class, hashtext(program_id)): one graph sync per program at a time
PROJECT_CHUNK = 200  # claims per Neo4j write transaction in the bulk path
SYNC_USER = "graph-sync"
NIL = UUID(int=0)

LOCK_PROGRAM_SQL = text("SELECT pg_advisory_xact_lock(:cls, hashtext(:program_id))")
VALID_IDS_SQL = text(
    "SELECT id FROM claims WHERE firm_id = :f AND program_id = :p AND status <> 'draft' AND id = ANY(:ids)"
)
FOREIGN_IDS_SQL = text("SELECT count(*) FROM claims WHERE id = ANY(:ids)")
ROWS_SQL = text(
    "SELECT id, member_claim_ids, score, created_at FROM fraud_clusters "
    "WHERE firm_id = :f AND program_id = :p AND kind = :k AND member_claim_ids && CAST(:ids AS uuid[]) "
    "ORDER BY created_at, id"
)
ALL_ROWS_SQL = text(
    "SELECT id, member_claim_ids, score, created_at FROM fraud_clusters "
    "WHERE firm_id = :f AND program_id = :p AND kind = :k ORDER BY created_at, id"
)
UPDATE_ROW_SQL = text(
    "UPDATE fraud_clusters SET member_claim_ids = CAST(:m AS uuid[]), score = :s WHERE id = :id AND firm_id = :f"
)
INSERT_ROW_SQL = text(
    "INSERT INTO fraud_clusters (firm_id, program_id, kind, member_claim_ids, score) "
    "VALUES (:f, :p, :k, CAST(:m AS uuid[]), :s)"
)
DELETE_ROWS_SQL = text("DELETE FROM fraud_clusters WHERE firm_id = :f AND id = ANY(:ids)")
FENCE_SQL = text(
    "UPDATE claims SET graph_synced_version = :v WHERE id = :c AND firm_id = :f AND graph_version = :v RETURNING id"
)
FENCE_MANY_SQL = text(
    "UPDATE claims AS c SET graph_synced_version = v.ver "
    "FROM unnest(CAST(:ids AS uuid[]), CAST(:vers AS integer[])) AS v(id, ver) "
    "WHERE c.id = v.id AND c.firm_id = :f AND c.graph_version = v.ver RETURNING c.id"
)
DIRTY_SQL = text(
    "SELECT id, firm_id, program_id FROM claims "
    "WHERE status <> 'draft' AND graph_synced_version IS DISTINCT FROM graph_version "
    "ORDER BY created_at, id LIMIT :n"
)
EPOCH_KEY_PREFIX = "graph_epoch:"


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers


def _photo_pairs(a: ClaimFacts, b: ClaimFacts, bits: int) -> list[tuple[str, int]]:
    """(photo kind, distance) for every same-kind pHash match between two claims. Byte-identical photos are left to
    the photo identity (SAME_PHOTO); receipts never match on pHash (check_duplicates, decisions log 2026-10-16)."""
    out: list[tuple[str, int]] = []
    for mine in a.photos:
        if mine.kind not in PHASH_KINDS:
            continue
        for theirs in b.photos:
            if theirs.kind != mine.kind or theirs.sha256 == mine.sha256:
                continue
            d = hamming(mine.phash, theirs.phash)
            if d <= bits:
                out.append((mine.kind, d))
    return out


def similar_photos(facts: ClaimFacts, population: Iterable[ClaimFacts], bits: int) -> list[SimilarPhoto]:
    """The claim's SIMILAR_PHOTO edges against the population; together with photo identities this is exactly the
    same_photo hit set of check_duplicates."""
    out: list[SimilarPhoto] = []
    for other in population:
        if other.claim_id == facts.claim_id:
            continue
        out.extend(SimilarPhoto(other.claim_id, kind, d) for kind, d in _photo_pairs(facts, other, bits))
    return sorted(set(out), key=lambda s: (str(s.other_claim_id), s.photo_kind))


def _similar_all(facts: Sequence[ClaimFacts], bits: int) -> dict[UUID, list[SimilarPhoto]]:
    out: dict[UUID, list[SimilarPhoto]] = {f.claim_id: [] for f in facts}
    for i, a in enumerate(facts):
        for b in facts[i + 1 :]:
            for kind, d in _photo_pairs(a, b, bits):
                out[a.claim_id].append(SimilarPhoto(b.claim_id, kind, d))
                out[b.claim_id].append(SimilarPhoto(a.claim_id, kind, d))
    return out


@dataclass(frozen=True)
class ClusterRow:
    id: UUID
    members: frozenset[UUID]
    score: Decimal
    created_at: datetime


@dataclass
class RowPlan:
    keep: list[tuple[ClusterRow, frozenset[UUID]]] = field(default_factory=list)
    insert: list[frozenset[UUID]] = field(default_factory=list)
    delete: list[ClusterRow] = field(default_factory=list)


def _piece_order(piece: frozenset[UUID]) -> tuple[int, list[str]]:
    return (-len(piece), sorted(str(x) for x in piece))


def plan_cluster_rows(existing: Iterable[ClusterRow], pieces: Iterable[frozenset[UUID]]) -> RowPlan:
    """Largest piece first: each piece keeps the oldest remaining row it meets (so a merge keeps the oldest id and
    a split keeps it for the largest piece); a piece meeting no row is inserted; rows left over are deleted."""
    available = sorted(existing, key=lambda r: (r.created_at, str(r.id)))
    plan = RowPlan()
    for piece in sorted({p for p in pieces if len(p) >= 2}, key=_piece_order):
        row = next((r for r in available if r.members & piece), None)
        if row is None:
            plan.insert.append(piece)
        else:
            available.remove(row)
            plan.keep.append((row, piece))
    plan.delete = available
    return plan


def pair_score(pairs: int, size: int) -> Decimal:
    possible = size * (size - 1) // 2
    if possible == 0:
        return Decimal("1")
    return (Decimal(pairs) / Decimal(possible)).quantize(Decimal("0.0001"))


@dataclass
class RowCounts:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0

    def add(self, other: RowCounts) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.deleted += other.deleted
        self.unchanged += other.unchanged


@dataclass(frozen=True)
class SyncResult:
    claim_id: UUID
    firm_id: UUID
    program_id: UUID
    outcome: Literal["synced", "stale", "skipped_draft", "missing"]
    version: int | None = None
    components: Mapping[GraphKind, frozenset[UUID]] = field(default_factory=dict)
    dropped: frozenset[UUID] = frozenset()
    rows: RowCounts = field(default_factory=RowCounts)


# ---------------------------------------------------------------------------------------------------------------
# Postgres side


def _lock_program(session: Session, program_id: UUID) -> None:
    session.execute(LOCK_PROGRAM_SQL, {"cls": SYNC_LOCK_CLASS, "program_id": str(program_id)})


def _trusted(
    session: Session,
    internal_engine: Engine | None,
    firm_id: UUID,
    program_id: UUID,
    ids: Iterable[UUID],
    *,
    source: str,
) -> set[UUID]:
    """Intersect graph output with the firm's non-draft claims of the program, read under RLS. Every dropped id is
    an error-level graph_tenant_violation; known_elsewhere counts dropped ids that are claims of another scope."""
    wanted = set(ids)
    if not wanted:
        return set()
    valid = set(session.scalars(VALID_IDS_SQL, {"f": firm_id, "p": program_id, "ids": sorted(wanted, key=str)}))
    dropped = wanted - valid
    if dropped:
        elsewhere = None
        if internal_engine is not None:
            with internal_engine.connect() as conn:
                elsewhere = int(conn.execute(FOREIGN_IDS_SQL, {"ids": sorted(dropped, key=str)}).scalar_one())
        log_event(
            log,
            "graph_tenant_violation",
            level=logging.ERROR,
            firm_id=str(firm_id),
            program_id=str(program_id),
            source=source,
            dropped=len(dropped),
            known_elsewhere=elsewhere,
            dropped_ids=sorted(str(d) for d in dropped)[:20],
        )
    return valid & wanted


def _rows(
    session: Session, firm_id: UUID, program_id: UUID, kind: GraphKind, ids: Iterable[UUID] | None
) -> list[ClusterRow]:
    if ids is None:
        result = session.execute(ALL_ROWS_SQL, {"f": firm_id, "p": program_id, "k": kind})
    else:
        result = session.execute(ROWS_SQL, {"f": firm_id, "p": program_id, "k": kind, "ids": sorted(ids, key=str)})
    return [ClusterRow(r.id, frozenset(r.member_claim_ids), Decimal(r.score), r.created_at) for r in result]


def _scores(
    store: GraphStore, ns: str, firm_id: UUID, program_id: UUID, kind: GraphKind, pieces: Iterable[frozenset[UUID]]
) -> dict[frozenset[UUID], Decimal]:
    out: dict[frozenset[UUID], Decimal] = {}
    for piece in pieces:
        n = len(piece)
        limit = max(1, n * (n - 1) // 2 * len(RING_REL_TYPES))
        edges = store.program_edges(ns, firm_id, program_id, piece, limit)
        pairs = {(e.a, e.b) for e in edges if e.kind == kind and e.a in piece and e.b in piece}
        out[piece] = pair_score(len(pairs), n)
    return out


def _apply_plan(
    session: Session,
    firm_id: UUID,
    program_id: UUID,
    kind: GraphKind,
    plan: RowPlan,
    scores: Mapping[frozenset[UUID], Decimal],
) -> RowCounts:
    counts = RowCounts()
    if plan.delete:
        session.execute(DELETE_ROWS_SQL, {"f": firm_id, "ids": [r.id for r in plan.delete]})
        counts.deleted = len(plan.delete)
    for row, piece in plan.keep:
        score = scores[piece]
        if row.members == piece and row.score == score:
            counts.unchanged += 1
            continue
        session.execute(UPDATE_ROW_SQL, {"id": row.id, "f": firm_id, "m": sorted(piece, key=str), "s": score})
        counts.updated += 1
    for piece in plan.insert:
        session.execute(
            INSERT_ROW_SQL,
            {"f": firm_id, "p": program_id, "k": kind, "m": sorted(piece, key=str), "s": scores[piece]},
        )
        counts.inserted += 1
    return counts


# ---------------------------------------------------------------------------------------------------------------
# One claim


def sync_claim(
    store: GraphStore,
    internal_engine: Engine | None,
    app_engine: Engine,
    settings: Settings,
    firm_id: UUID,
    program_id: UUID,
    claim_id: UUID,
) -> SyncResult:
    """Project one claim and rewrite the fraud_clusters rows its components touch. GraphUnavailable propagates
    (the Postgres transaction rolls back; the claim stays dirty). internal_engine only classifies dropped ids."""
    ns = settings.neo4j_namespace
    t0 = time.monotonic()
    ctx = FirmContext(firm_id, SYNC_USER, "admin")
    with tenant_session(ctx, app_engine) as session:
        _lock_program(session, program_id)
        claim = session.scalars(select(Claim).where(Claim.id == claim_id, Claim.firm_id == firm_id)).one_or_none()
        if claim is None or claim.program_id != program_id:
            log_event(log, "graph_sync_missing", level=logging.WARNING, firm_id=str(firm_id), claim_id=str(claim_id))
            return SyncResult(claim_id, firm_id, program_id, "missing")
        if claim.status == "draft":
            return SyncResult(claim_id, firm_id, program_id, "skipped_draft", claim.graph_version)
        version = claim.graph_version
        photos = list(
            session.scalars(select(ClaimPhoto).where(ClaimPhoto.claim_id == claim_id, ClaimPhoto.firm_id == firm_id))
        )
        facts = facts_from_rows(claim, photos)
        population = program_claims(session, firm_id, program_id, claim_id)
        similar = similar_photos(facts, population, settings.phash_duplicate_bits)
        mailing = (claim.claimant or {}).get("mailing_address")
        store.project_claim(ns, firm_id, program_id, facts, mailing if isinstance(mailing, str) else None, similar)

        components: dict[GraphKind, frozenset[UUID]] = {}
        dropped: set[UUID] = set()
        counts = RowCounts()
        for kind in GRAPH_KINDS:
            raw = store.components(ns, firm_id, program_id, claim_id, kind)
            seed = frozenset(_trusted(session, internal_engine, firm_id, program_id, raw, source=kind) | {claim_id})
            dropped |= raw - seed
            components[kind] = seed
            pieces, rows, more_dropped = _closure(store, session, internal_engine, ns, firm_id, program_id, kind, seed)
            dropped |= more_dropped
            plan = plan_cluster_rows(rows, pieces)
            scores = _scores(store, ns, firm_id, program_id, kind, [piece for _, piece in plan.keep] + plan.insert)
            counts.add(_apply_plan(session, firm_id, program_id, kind, plan, scores))

        fenced = session.execute(FENCE_SQL, {"v": version, "c": claim_id, "f": firm_id}).first() is not None
        outcome: Literal["synced", "stale"] = "synced" if fenced else "stale"
    log_event(
        log,
        "graph_sync_claim",
        firm_id=str(firm_id),
        claim_id=str(claim_id),
        outcome=outcome,
        version=version,
        inserted=counts.inserted,
        updated=counts.updated,
        deleted=counts.deleted,
        dropped=len(dropped),
        duration_ms=round((time.monotonic() - t0) * 1000),
    )
    return SyncResult(claim_id, firm_id, program_id, outcome, version, components, frozenset(dropped), counts)


def _closure(
    store: GraphStore,
    session: Session,
    internal_engine: Engine | None,
    ns: str,
    firm_id: UUID,
    program_id: UUID,
    kind: GraphKind,
    seed: frozenset[UUID],
) -> tuple[list[frozenset[UUID]], list[ClusterRow], set[UUID]]:
    """Every existing row of the kind that meets the component, every component that meets those rows, until
    nothing new appears. The pieces are the current components of all affected claims (merges and splits)."""
    piece_of: dict[UUID, frozenset[UUID]] = dict.fromkeys(seed, seed)
    rows: dict[UUID, ClusterRow] = {}
    dropped: set[UUID] = set()
    frontier: set[UUID] = set(seed)
    while frontier:
        found = [r for r in _rows(session, firm_id, program_id, kind, frontier) if r.id not in rows]
        frontier = set()
        for row in found:
            rows[row.id] = row
            fresh = sorted((m for m in row.members if m not in piece_of), key=str)
            alive = _trusted(session, None, firm_id, program_id, fresh, source=f"{kind}:row") if fresh else set()
            for member in fresh:
                if member not in alive or member in piece_of:
                    continue
                raw = store.components(ns, firm_id, program_id, member, kind)
                comp = frozenset(_trusted(session, internal_engine, firm_id, program_id, raw, source=kind) | {member})
                dropped |= raw - comp
                for x in comp:
                    piece_of[x] = comp
                frontier |= comp
    return sorted(set(piece_of.values()), key=_piece_order), list(rows.values()), dropped


# ---------------------------------------------------------------------------------------------------------------
# Bulk (seed, rebuild), resync, epoch


def sync_program(
    store: GraphStore,
    internal_engine: Engine | None,
    app_engine: Engine,
    settings: Settings,
    firm_id: UUID,
    program_id: UUID,
) -> dict[str, Any]:
    """Rebuild one program: one population load, projection in chunks, purge of claim nodes Postgres no longer has,
    one components query per kind, and the program's fraud_clusters rewritten in the same Postgres transaction.
    Every claim is marked synced at the version read (fenced). No status changes."""
    ns = settings.neo4j_namespace
    t0 = time.monotonic()
    ctx = FirmContext(firm_id, SYNC_USER, "admin")
    counts = RowCounts()
    per_kind: dict[str, int] = {}
    dropped = 0
    with tenant_session(ctx, app_engine) as session:
        _lock_program(session, program_id)
        claims = list(
            session.scalars(
                select(Claim)
                .where(Claim.firm_id == firm_id, Claim.program_id == program_id, Claim.status != "draft")
                .order_by(Claim.id)
            )
        )
        photos: dict[UUID, list[ClaimPhoto]] = {c.id: [] for c in claims}
        if claims:
            for p in session.scalars(
                select(ClaimPhoto).where(ClaimPhoto.firm_id == firm_id, ClaimPhoto.claim_id.in_(list(photos)))
            ):
                photos[p.claim_id].append(p)
        facts = [facts_from_rows(c, photos[c.id]) for c in claims]
        similar = _similar_all(facts, settings.phash_duplicate_bits)
        items = []
        for c, f in zip(claims, facts, strict=True):
            mailing = (c.claimant or {}).get("mailing_address")
            items.append(ClaimProjection(f, mailing if isinstance(mailing, str) else None, tuple(similar[c.id])))
        for chunk in batched(items, PROJECT_CHUNK, strict=False):
            store.project_claims(ns, firm_id, program_id, list(chunk))
        purged = purge_absent(store, ns, firm_id, program_id, [c.id for c in claims])

        for kind in GRAPH_KINDS:
            pieces = []
            for comp in store.program_components(ns, firm_id, program_id, kind):
                kept = frozenset(
                    _trusted(session, internal_engine, firm_id, program_id, comp, source=f"program:{kind}")
                )
                dropped += len(comp) - len(kept)
                if len(kept) >= 2:
                    pieces.append(kept)
            per_kind[kind] = len(pieces)
            plan = plan_cluster_rows(_rows(session, firm_id, program_id, kind, None), pieces)
            scores = _scores(store, ns, firm_id, program_id, kind, [piece for _, piece in plan.keep] + plan.insert)
            counts.add(_apply_plan(session, firm_id, program_id, kind, plan, scores))

        synced = 0
        if claims:
            synced = len(
                session.execute(
                    FENCE_MANY_SQL,
                    {"f": firm_id, "ids": [c.id for c in claims], "vers": [c.graph_version for c in claims]},
                ).all()
            )
    summary: dict[str, Any] = {
        "firm_id": str(firm_id),
        "program_id": str(program_id),
        "claims": len(claims),
        "synced": synced,
        "purged": purged,
        "components": per_kind,
        "rows": {
            "inserted": counts.inserted,
            "updated": counts.updated,
            "deleted": counts.deleted,
            "unchanged": counts.unchanged,
        },
        "dropped": dropped,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }
    log_event(
        log,
        "graph_sync_program",
        firm_id=str(firm_id),
        program_id=str(program_id),
        claims=len(claims),
        synced=synced,
        purged=purged,
        dropped=dropped,
        duration_ms=summary["duration_ms"],
    )
    return summary


def resync_dirty(
    store: GraphStore,
    internal_engine: Engine,
    app_engine: Engine,
    settings: Settings,
    limit: int,
    heartbeat: Callable[[], None] | None = None,
) -> int:
    """Re-project up to `limit` dirty non-draft claims across firms, oldest first. Stops at the first
    GraphUnavailable and re-raises it (the breaker then makes the next tick fail fast). Returns claims processed."""
    with internal_engine.connect() as conn:
        dirty = conn.execute(DIRTY_SQL, {"n": limit}).all()
    done = 0
    for row in dirty:
        try:
            sync_claim(store, internal_engine, app_engine, settings, row.firm_id, row.program_id, row.id)
        except GraphUnavailable:
            log_event(log, "graph_resync_stopped", level=logging.WARNING, done=done, pending=len(dirty) - done)
            raise
        done += 1
        if heartbeat is not None:
            heartbeat()
    if dirty:
        log_event(log, "graph_resync", done=done)
    return done


def check_epoch(store: GraphStore, internal_engine: Engine, settings: Settings) -> bool:
    """Compare Postgres graph_state('graph_epoch:<ns>') with the GraphMeta epoch. On a mismatch or a missing value
    (an emptied, replaced, or never-initialized graph) mark every claim dirty and write a fresh epoch to both.
    Returns True when it reset."""
    ns = settings.neo4j_namespace
    key = EPOCH_KEY_PREFIX + ns
    graph_epoch = get_epoch(store, ns)
    with internal_engine.connect() as conn:
        pg_epoch = conn.execute(text("SELECT value FROM graph_state WHERE key = :k"), {"k": key}).scalar_one_or_none()
    if graph_epoch is not None and graph_epoch == pg_epoch:
        return False
    epoch = uuid.uuid4().hex
    with internal_engine.begin() as conn:
        marked = conn.execute(
            text("UPDATE claims SET graph_synced_version = NULL WHERE graph_synced_version IS NOT NULL RETURNING id")
        ).all()
        conn.execute(
            text(
                "INSERT INTO graph_state (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()"
            ),
            {"k": key, "v": epoch},
        )
        set_epoch(store, ns, epoch)  # inside the Postgres transaction: a graph failure leaves both sides unchanged
    log_event(
        log,
        "graph_epoch_reset",
        level=logging.WARNING,
        namespace=ns,
        had_graph_epoch=graph_epoch is not None,
        had_pg_epoch=pg_epoch is not None,
        claims_marked=len(marked),
    )
    return True
