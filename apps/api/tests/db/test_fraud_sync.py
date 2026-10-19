"""Projection sync through Postgres and Neo4j: the adversarial fixture, incremental against bulk, merges, splits,
the version fence, the epoch, untrusted graph output, the console view, and the CLI."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops.db.session import tenant_session
from recallops.fraud.__main__ import main as fraud_cli
from recallops.fraud.cypher import GRAPH_KINDS, GraphKind
from recallops.fraud.graph import GraphError, GraphUnavailable, Neo4jGraphStore
from recallops.fraud.graph_admin import purge_namespace
from recallops.fraud.sync import check_epoch, resync_dirty, sync_claim, sync_program
from recallops.fraud.view import fraud_graph
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from tests.fixtures.fraud_claims import (
    NS,
    ClaimRow,
    add_photos,
    adversarial_claims,
    cid,
    insert_claims,
    insert_firm,
    person,
    reupload,
)

pytestmark = [pytest.mark.db, pytest.mark.graph]

FIRM = uuid.uuid5(NS, "adversarial-firm")
PROGRAM = uuid.uuid5(NS, "adversarial-program")
OTHER_FIRM = uuid.uuid5(NS, "other-firm")
OTHER_PROGRAM = uuid.uuid5(NS, "other-program")


def S(*names: str) -> frozenset[UUID]:
    return frozenset(cid(n) for n in names)


# Pre-registered expected components of the adversarial fixture (written before the first run).
# Before reup_b re-uploads its label photo:
EXPECTED_BEFORE: dict[str, set[frozenset[UUID]]] = {
    "same_photo": {S("chain1", "chain2", "chain3"), S("reup_a", "reup_b")},  # chain: ends 8 bits apart
    "same_address": {S("email1", "email2"), S("ring_a", "ring_b")},  # email-only match counts as same_address
    "same_serial": set(),  # "-" and " - " normalize to "": no identity; the draft's serial is not projected
    "same_payout": {S("ring_b", "ring_c")},
    "same_mailing": {S("mail1", "mail2")},
}
EXPECTED_RINGS_BEFORE = {
    S("chain1", "chain2", "chain3"),
    S("reup_a", "reup_b"),
    S("email1", "email2"),
    S("ring_a", "ring_b", "ring_c"),
    S("mail1", "mail2"),
}
# After the re-upload and a resync: the old photo link is gone, nothing else moves.
EXPECTED_AFTER = {**EXPECTED_BEFORE, "same_photo": {S("chain1", "chain2", "chain3")}}
EXPECTED_RINGS_AFTER = EXPECTED_RINGS_BEFORE - {S("reup_a", "reup_b")}


def ctx(firm: UUID = FIRM) -> FirmContext:
    return FirmContext(firm, "test", "admin")


def graph_components(
    store: Neo4jGraphStore, firm: UUID = FIRM, program: UUID = PROGRAM
) -> dict[str, set[frozenset[UUID]]]:
    ns = get_settings().neo4j_namespace
    return {kind: set(store.program_components(ns, firm, program, kind)) for kind in GRAPH_KINDS}


def stored_clusters(engine: Engine, firm: UUID = FIRM) -> dict[str, set[frozenset[UUID]]]:
    out: dict[str, set[frozenset[UUID]]] = {k: set() for k in GRAPH_KINDS}
    with engine.connect() as conn:
        for kind, members in conn.execute(
            text("SELECT kind, member_claim_ids FROM fraud_clusters WHERE firm_id = :f"), {"f": firm}
        ):
            out[kind].add(frozenset(members))
    return out


def cluster_rows(engine: Engine, firm: UUID = FIRM) -> list[tuple[UUID, str, frozenset[UUID], Any]]:
    with engine.connect() as conn:
        return [
            (r.id, r.kind, frozenset(r.member_claim_ids), r.score)
            for r in conn.execute(
                text("SELECT id, kind, member_claim_ids, score FROM fraud_clusters WHERE firm_id = :f ORDER BY id"),
                {"f": firm},
            )
        ]


def dirty(engine: Engine, firm: UUID = FIRM) -> set[UUID]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT id FROM claims WHERE firm_id = :f AND status <> 'draft' "
                    "AND graph_synced_version IS DISTINCT FROM graph_version"
                ),
                {"f": firm},
            ).scalars()
        )


@pytest.fixture()
def adversarial(clean_db: Engine) -> list[ClaimRow]:
    claims = adversarial_claims()
    with clean_db.begin() as conn:
        insert_firm(conn, FIRM, "Adversarial", [PROGRAM])
        insert_claims(conn, FIRM, PROGRAM, claims)
    return claims


def test_adversarial_fixture_matches_the_preregistered_components(
    adversarial: list[ClaimRow], clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    summary = sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    assert summary["claims"] == len(adversarial) - 1  # the draft is not projected
    assert graph_components(graph_store) == EXPECTED_BEFORE
    assert stored_clusters(clean_db) == EXPECTED_BEFORE
    ns = settings.neo4j_namespace
    assert set(graph_store.program_rings(ns, FIRM, PROGRAM)) == EXPECTED_RINGS_BEFORE
    assert dirty(clean_db) == set()
    scores = {(kind, members): score for _, kind, members, score in cluster_rows(clean_db)}
    assert float(scores[("same_photo", S("chain1", "chain2", "chain3"))]) == pytest.approx(0.6667)
    assert float(scores[("same_address", S("ring_a", "ring_b"))]) == 1.0

    # needs_info re-upload: reup_b sends a new label photo (latest per kind wins); the trigger marks it dirty.
    reup_b = next(c for c in adversarial if c.id == cid("reup_b"))
    with clean_db.begin() as conn:
        add_photos(conn, FIRM, [reupload(reup_b)])
    assert dirty(clean_db) == {cid("reup_b")}
    beats: list[int] = []
    assert resync_dirty(graph_store, clean_db, app_engine_test, settings, 50, heartbeat=lambda: beats.append(1)) == 1
    assert beats == [1]
    assert graph_components(graph_store) == EXPECTED_AFTER
    assert stored_clusters(clean_db) == EXPECTED_AFTER
    assert set(graph_store.program_rings(ns, FIRM, PROGRAM)) == EXPECTED_RINGS_AFTER
    assert dirty(clean_db) == set()


def test_incremental_sync_in_reverse_order_equals_the_bulk_sync(
    adversarial: list[ClaimRow], clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    bulk = {(kind, members, score) for _, kind, members, score in cluster_rows(clean_db)}
    purge_namespace(graph_store, settings.neo4j_namespace)
    with clean_db.begin() as conn:
        conn.execute(text("DELETE FROM fraud_clusters WHERE firm_id = :f"), {"f": FIRM})
        conn.execute(text("UPDATE claims SET graph_synced_version = NULL WHERE firm_id = :f"), {"f": FIRM})
    outcomes = [
        sync_claim(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM, c.id).outcome
        for c in sorted(adversarial, key=lambda c: str(c.id), reverse=True)
    ]
    assert outcomes.count("skipped_draft") == 1 and outcomes.count("synced") == len(adversarial) - 1
    incremental = {(kind, members, score) for _, kind, members, score in cluster_rows(clean_db)}
    assert incremental == bulk
    assert graph_components(graph_store) == EXPECTED_BEFORE
    assert dirty(clean_db) == set()


def _claims(firm: UUID, program: UUID, specs: dict[str, dict[str, Any]], engine: Engine) -> None:
    with engine.begin() as conn:
        insert_firm(conn, firm, "Firm", [program])
        insert_claims(conn, firm, program, [ClaimRow(cid(name), claimant=c) for name, c in specs.items()])


def test_merge_keeps_the_oldest_cluster_id(
    clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    x = {"address_line1": "5 Merge Ave", "city": "M", "state": "CA", "zip": "90005"}
    y = {"address_line1": "6 Merge Ave", "city": "M", "state": "CA", "zip": "90006"}
    _claims(
        FIRM,
        PROGRAM,
        {
            "m_a": person(101, **x),
            "m_b": person(102, **x),
            "m_c": person(103, **y, email="shared-merge@example.org"),
            "m_d": person(104, **y),
            "m_e": person(105, **x, email="shared-merge@example.org"),  # links {a, b} and {c, d}
        },
        clean_db,
    )

    def sync(name: str) -> None:
        sync_claim(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM, cid(name))

    sync("m_a")
    sync("m_b")
    first = cluster_rows(clean_db)
    assert [(k, m) for _, k, m, _ in first] == [("same_address", S("m_a", "m_b"))]
    sync("m_c")
    sync("m_d")
    assert {m for _, _, m, _ in cluster_rows(clean_db)} == {S("m_a", "m_b"), S("m_c", "m_d")}
    sync("m_e")
    after = cluster_rows(clean_db)
    assert [(i, k, m) for i, k, m, _ in after] == [(first[0][0], "same_address", S("m_a", "m_b", "m_c", "m_d", "m_e"))]


def test_split_rewrites_both_pieces(clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore) -> None:
    settings = get_settings()
    x = {"address_line1": "7 Split St", "city": "S", "state": "CA", "zip": "90007"}
    y = {"address_line1": "8 Split St", "city": "S", "state": "CA", "zip": "90008"}
    _claims(
        FIRM,
        PROGRAM,
        {
            "s_a": person(111, **x),
            "s_b": person(112, **x, email="bridge@example.org"),
            "s_c": person(113, **y, email="bridge@example.org"),
            "s_d": person(114, **y),
        },
        clean_db,
    )
    sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    (original,) = cluster_rows(clean_db)
    assert original[2] == S("s_a", "s_b", "s_c", "s_d")
    with clean_db.begin() as conn:  # s_b changes its email: the bridge is gone
        conn.execute(
            text('UPDATE claims SET claimant = claimant || \'{"email": "s_b-new@example.org"}\' WHERE id = :c'),
            {"c": cid("s_b")},
        )
    assert dirty(clean_db) == {cid("s_b")}
    result = sync_claim(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM, cid("s_b"))
    assert result.outcome == "synced" and result.components["same_address"] == S("s_a", "s_b")
    rows = cluster_rows(clean_db)
    assert {m for _, _, m, _ in rows} == {S("s_a", "s_b"), S("s_c", "s_d")}
    assert sum(1 for i, *_ in rows if i == original[0]) == 1  # the old id survives on one piece
    assert result.rows.updated == 1 and result.rows.inserted == 1


@pytest.fixture()
def misbehaving(graph_store: Neo4jGraphStore) -> Iterator[list[Neo4jGraphStore]]:
    """Stores built by a test (subclasses of the real store that misbehave in one method), closed afterwards."""
    made: list[Neo4jGraphStore] = []
    yield made
    for store in made:
        store.close()


def test_version_fence_leaves_a_claim_dirty_when_it_changed_during_the_sync(
    adversarial: list[ClaimRow],
    clean_db: Engine,
    app_engine_test: Engine,
    graph_store: Neo4jGraphStore,
    misbehaving: list[Neo4jGraphStore],
) -> None:
    settings = get_settings()

    class ChangesDuringSync(Neo4jGraphStore):
        def project_claim(self, *args: Any, **kwargs: Any) -> None:
            super().project_claim(*args, **kwargs)
            with clean_db.begin() as conn:  # a portal edit commits while the sync transaction is open
                conn.execute(text("UPDATE claims SET serial_claimed = 'CHANGED-1' WHERE id = :c"), {"c": cid("chain1")})

    store = ChangesDuringSync(settings)
    misbehaving.append(store)
    result = sync_claim(store, clean_db, app_engine_test, settings, FIRM, PROGRAM, cid("chain1"))
    assert result.outcome == "stale"
    assert cid("chain1") in dirty(clean_db)
    again = sync_claim(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM, cid("chain1"))
    assert again.outcome == "synced" and again.version == (result.version or 0) + 1
    assert cid("chain1") not in dirty(clean_db)


def test_check_epoch_marks_everything_dirty_after_the_graph_is_emptied(
    adversarial: list[ClaimRow], clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    assert check_epoch(graph_store, clean_db, settings) is True  # first contact: no GraphMeta yet
    sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    assert dirty(clean_db) == set()
    assert check_epoch(graph_store, clean_db, settings) is False
    assert dirty(clean_db) == set()
    purge_namespace(graph_store, settings.neo4j_namespace)  # an emptied or replaced graph
    assert check_epoch(graph_store, clean_db, settings) is True
    assert dirty(clean_db) == {c.id for c in adversarial if c.status != "draft"}
    assert check_epoch(graph_store, clean_db, settings) is False


def test_a_foreign_id_returned_by_the_graph_is_dropped(
    adversarial: list[ClaimRow],
    clean_db: Engine,
    app_engine_test: Engine,
    graph_store: Neo4jGraphStore,
    misbehaving: list[Neo4jGraphStore],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = get_settings()
    foreign = cid("other-firm-claim")
    with clean_db.begin() as conn:
        insert_firm(conn, OTHER_FIRM, "Other", [OTHER_PROGRAM])
        insert_claims(
            conn, OTHER_FIRM, OTHER_PROGRAM, [ClaimRow(foreign, claimant=person(1), serial_claimed="SER0001")]
        )
    sync_program(graph_store, clean_db, app_engine_test, settings, OTHER_FIRM, OTHER_PROGRAM)
    sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)

    def snapshot() -> tuple[list[Any], list[Any]]:
        with clean_db.connect() as conn:
            claims = conn.execute(
                text("SELECT id, status, graph_version, graph_synced_version FROM claims WHERE firm_id = :f"),
                {"f": OTHER_FIRM},
            ).all()
        return [tuple(r) for r in claims], cluster_rows(clean_db, OTHER_FIRM)

    before = snapshot()

    class LeaksForeignIds(Neo4jGraphStore):
        def components(self, ns: str, firm_id: UUID, program_id: UUID, claim_id: UUID, kind: GraphKind) -> set[UUID]:
            return super().components(ns, firm_id, program_id, claim_id, kind) | {foreign}

        def program_components(
            self, ns: str, firm_id: UUID, program_id: UUID, kind: GraphKind
        ) -> list[frozenset[UUID]]:
            comps = super().program_components(ns, firm_id, program_id, kind)
            return [c | {foreign} for c in comps] or [frozenset({cid("chain1"), foreign})]

        def program_rings(self, ns: str, firm_id: UUID, program_id: UUID) -> list[frozenset[UUID]]:
            return [r | {foreign} for r in super().program_rings(ns, firm_id, program_id)]

    leaky = LeaksForeignIds(settings)
    misbehaving.append(leaky)
    caplog.set_level(logging.ERROR, logger="recallops.fraud")
    result = sync_claim(leaky, clean_db, app_engine_test, settings, FIRM, PROGRAM, cid("chain1"))
    assert foreign in result.dropped
    assert all(foreign not in comp for comp in result.components.values())
    summary = sync_program(leaky, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    assert summary["dropped"] > 0
    violations = [r for r in caplog.records if r.getMessage() == "graph_tenant_violation"]
    assert violations and all(r.levelno == logging.ERROR for r in violations)
    assert all(foreign not in m for _, _, m, _ in cluster_rows(clean_db))
    assert stored_clusters(clean_db) == EXPECTED_BEFORE
    with tenant_session(ctx(), app_engine_test) as session:
        view = fraud_graph(session, leaky, ctx(), PROGRAM)
    assert foreign not in {n.claim_id for n in view.nodes}
    assert snapshot() == before


def test_fraud_graph_view_reads_statuses_from_postgres_and_caps_nodes(
    adversarial: list[ClaimRow], clean_db: Engine, app_engine_test: Engine, graph_store: Neo4jGraphStore
) -> None:
    settings = get_settings()
    sync_program(graph_store, clean_db, app_engine_test, settings, FIRM, PROGRAM)
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'denied' WHERE id = :c"), {"c": cid("ring_c")})
    with tenant_session(ctx(), app_engine_test) as session:
        view = fraud_graph(session, graph_store, ctx(), PROGRAM)
        capped = fraud_graph(session, graph_store, ctx(), PROGRAM, node_cap=3)
    assert {frozenset(r) for r in view.rings} == EXPECTED_RINGS_BEFORE
    assert not view.truncated
    nodes = {n.claim_id: n for n in view.nodes}
    assert set(nodes) == set().union(*EXPECTED_RINGS_BEFORE)
    assert nodes[cid("ring_c")].status == "denied" and nodes[cid("ring_a")].status == "submitted"
    assert nodes[cid("ring_b")].cluster_kinds == ["same_address", "same_payout"]
    assert {(frozenset({e.a, e.b}), e.kind) for e in view.edges} >= {
        (S("ring_a", "ring_b"), "same_address"),
        (S("ring_b", "ring_c"), "same_payout"),
        (S("chain1", "chain2"), "same_photo"),
        (S("mail1", "mail2"), "same_mailing"),
    }
    assert (S("chain1", "chain3"), "same_photo") not in {(frozenset({e.a, e.b}), e.kind) for e in view.edges}
    assert capped.truncated and len(capped.nodes) <= 3 and len(capped.rings) == 1
    with tenant_session(ctx(OTHER_FIRM), app_engine_test) as session:  # another firm sees nothing of this program
        empty = fraud_graph(session, graph_store, ctx(OTHER_FIRM), PROGRAM)
    assert empty.nodes == [] and empty.rings == []


def test_resync_stops_at_the_first_graph_failure(
    adversarial: list[ClaimRow],
    clean_db: Engine,
    app_engine_test: Engine,
    graph_store: Neo4jGraphStore,
    misbehaving: list[Neo4jGraphStore],
) -> None:
    settings = get_settings()
    calls: list[UUID] = []

    class Down(Neo4jGraphStore):
        def project_claim(self, *args: Any, **kwargs: Any) -> None:
            calls.append(args[3].claim_id)
            raise GraphUnavailable("down")

    before = dirty(clean_db)
    with pytest.raises(GraphUnavailable):
        down = Down(settings)
        misbehaving.append(down)
        resync_dirty(down, clean_db, app_engine_test, settings, 50)
    assert len(calls) == 1 and dirty(clean_db) == before  # rolled back; nothing marked synced


def test_resync_skips_a_claim_the_graph_rejects(
    adversarial: list[ClaimRow],
    clean_db: Engine,
    app_engine_test: Engine,
    graph_store: Neo4jGraphStore,
    misbehaving: list[Neo4jGraphStore],
) -> None:
    settings = get_settings()

    class RejectsOne(Neo4jGraphStore):
        def project_claim(self, *args: Any, **kwargs: Any) -> None:
            if args[3].claim_id == cid("chain2"):
                raise GraphError("rejected")
            super().project_claim(*args, **kwargs)

    store = RejectsOne(settings)
    misbehaving.append(store)
    synced = resync_dirty(store, clean_db, app_engine_test, settings, 50)
    assert synced == len(adversarial) - 2  # every non-draft claim but the rejected one
    assert dirty(clean_db) == {cid("chain2")}


def test_cli_sync_and_status(
    adversarial: list[ClaimRow],
    clean_db: Engine,
    graph_store: Neo4jGraphStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert fraud_cli(["sync", "--program", str(PROGRAM)]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["program_id"] == str(PROGRAM) and lines[0]["claims"] == len(adversarial) - 1
    assert graph_components(graph_store) == EXPECTED_BEFORE
    assert fraud_cli(["sync", "--all"]) == 0
    capsys.readouterr()
    assert fraud_cli(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["graph"] == "ok" and status["dirty_claims"] == 0 and status["graph_epoch"] == status["pg_epoch"]
