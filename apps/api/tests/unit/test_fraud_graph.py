"""Fraud graph projection semantics on the in-memory store (no Neo4j, no Postgres)."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from recallops.claims.tools import ClaimFacts, PhotoRef, check_duplicates
from recallops.fraud.cypher import (
    COMPONENT,
    GRAPH_KINDS,
    IDENTITY_REL,
    LINK_IDENTITY,
    PROGRAM_COMPONENTS,
    RING,
    TENANT_STATEMENTS,
)
from recallops.fraud.graph import (
    ClaimProjection,
    InMemoryGraphStore,
    build_graph_store,
    firm_key,
    graph_status,
    identity_key,
    identity_links,
)
from recallops.fraud.graph_admin import get_epoch, graph_programs, purge_absent, purge_firm, purge_program, set_epoch
from recallops.fraud.sync import ClusterRow, pair_score, plan_cluster_rows, similar_photos
from recallops.settings import EXAMPLE_GRAPH_KEY_SECRET, Settings

SECRET = b"unit-test-graph-key-secret-of-32-bytes"
NS = "test_unit"
FIRM_A, FIRM_B = uuid.UUID(int=0xA), uuid.UUID(int=0xB)
PROG_A, PROG_B = uuid.UUID(int=0xA0), uuid.UUID(int=0xB0)


def facts(n: int, **kw: object) -> ClaimFacts:
    base: dict[str, object] = {
        "claim_id": uuid.UUID(int=n + 1000),
        "email": f"p{n}@example.org",
        "address": f"{n} Elm St Town CA",
        "serial_claimed": f"SN{n:05d}",
        "payout_destination": f"dest_{n}",
    }
    base.update(kw)
    return ClaimFacts.model_validate(base)


def photo(kind: str, sha: str, ph: str) -> PhotoRef:
    return PhotoRef.model_validate({"kind": kind, "sha256": sha, "phash": ph})


# --- identity keys ------------------------------------------------------------------------------------------------


def test_identity_keys_differ_across_firms_for_the_same_value() -> None:
    f = facts(1)
    a = {link.identity_kind: link.key for link in identity_links(SECRET, FIRM_A, f)}
    b = {link.identity_kind: link.key for link in identity_links(SECRET, FIRM_B, f)}
    assert a.keys() == b.keys() == {"address", "email", "serial", "payout"}
    assert all(a[k] != b[k] for k in a)
    assert firm_key(SECRET, FIRM_A) != firm_key(SECRET, FIRM_B)
    # Keys are HMACs, never the raw or normalized value.
    raw = {"1elmsttownca", "p1exampleorg", "SN00001", "dest_1"}
    assert not raw & set(a.values())
    assert a["serial"] == identity_key(firm_key(SECRET, FIRM_A), "serial", "SN00001")


def test_empty_normalized_values_create_no_identity() -> None:
    f = facts(2, email=" .@. ", address="--", serial_claimed=" - ", payout_destination="")
    assert identity_links(SECRET, FIRM_A, f, mailing_address="  ,  ") == []
    store = InMemoryGraphStore(SECRET)
    g = facts(3, email=" .@. ", address="--", serial_claimed="-", payout_destination="")
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(f, "  ,  "), ClaimProjection(g, ",")])
    for kind in GRAPH_KINDS:
        assert store.components(NS, FIRM_A, PROG_A, f.claim_id, kind) == {f.claim_id}
    assert [n for n in store.nodes.values() if n.label == "Identity"] == []


def test_photo_identities_are_kind_scoped() -> None:
    same = "ab" * 32
    a = facts(4, photos=(photo("label", same, "0" * 16),))
    b = facts(5, photos=(photo("destruction", same, "0" * 16),))
    la = identity_links(SECRET, FIRM_A, a)
    lb = identity_links(SECRET, FIRM_A, b)
    photo_a = next(link for link in la if link.kind == "same_photo")
    photo_b = next(link for link in lb if link.kind == "same_photo")
    assert (photo_a.identity_kind, photo_b.identity_kind) == ("photo_label", "photo_destruction")
    assert photo_a.key != photo_b.key
    store = InMemoryGraphStore(SECRET)
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(a), ClaimProjection(b)])
    assert store.components(NS, FIRM_A, PROG_A, a.claim_id, "same_photo") == {a.claim_id}
    assert check_duplicates(a, [b]).hits == ()  # the frozen tool agrees: different kinds never compare


def _random_population(seed: int, n: int = 60) -> list[ClaimFacts]:
    rng = random.Random(seed)
    pool: list[PhotoRef] = []
    out = []
    for i in range(n):
        photos = []
        for kind in ("label", "destruction", "receipt", "other"):
            if rng.random() < 0.3:
                continue
            roll = rng.random()
            if pool and roll < 0.15:  # exact copy of an earlier photo of the same kind
                same = [p for p in pool if p.kind == kind]
                if same:
                    photos.append(rng.choice(same))
                    continue
            if pool and roll < 0.35:  # near copy: flip 0 to 9 bits of an earlier pHash, new bytes
                same = [p for p in pool if p.kind == kind]
                if same:
                    src = rng.choice(same)
                    mask = sum(1 << b for b in rng.sample(range(64), rng.randint(0, 9)))
                    photos.append(photo(kind, f"{rng.getrandbits(256):064x}", f"{int(src.phash, 16) ^ mask:016x}"))
                    continue
            photos.append(photo(kind, f"{rng.getrandbits(256):064x}", f"{rng.getrandbits(64):016x}"))
        pool.extend(photos)
        out.append(facts(i, photos=tuple(photos)))
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_similar_photos_mirror_check_duplicates(seed: int) -> None:
    population = _random_population(seed)
    checked = 0
    for claim in population:
        expected = {h.other_claim_id for h in check_duplicates(claim, population).hits if h.kind == "same_photo"}
        by_identity = {
            o.claim_id
            for o in population
            if o.claim_id != claim.claim_id
            and any(m.kind == t.kind and m.sha256 == t.sha256 for m in claim.photos for t in o.photos)
        }
        by_phash = {s.other_claim_id for s in similar_photos(claim, population, 6)}
        assert by_identity | by_phash == expected
        checked += len(expected)
    assert checked > 0  # the population really contains duplicates

    # And the projected graph's same_photo components are the connected components of those hits.
    store = InMemoryGraphStore(SECRET)
    store.project_claims(
        NS,
        FIRM_A,
        PROG_A,
        [ClaimProjection(f, None, tuple(similar_photos(f, population, 6))) for f in population],
    )
    expected_comps = _union_find(
        population,
        [
            (c.claim_id, h.other_claim_id)
            for c in population
            for h in check_duplicates(c, population).hits
            if h.kind == "same_photo"
        ],
    )
    assert set(store.program_components(NS, FIRM_A, PROG_A, "same_photo")) == expected_comps


def _union_find(population: list[ClaimFacts], pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> set[frozenset[uuid.UUID]]:
    parent = {f.claim_id: f.claim_id for f in population}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[uuid.UUID, set[uuid.UUID]] = {}
    for f in population:
        groups.setdefault(find(f.claim_id), set()).add(f.claim_id)
    return {frozenset(g) for g in groups.values() if len(g) >= 2}


# --- store semantics ----------------------------------------------------------------------------------------------


def test_reprojection_replaces_links_and_deletes_orphan_identities() -> None:
    store = InMemoryGraphStore(SECRET)
    a, b = facts(10, serial_claimed="DUP"), facts(11, serial_claimed="DUP")
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(a), ClaimProjection(b)])
    assert store.components(NS, FIRM_A, PROG_A, a.claim_id, "same_serial") == {a.claim_id, b.claim_id}

    def serial_keys() -> set[str]:
        return {n.props["key"] for n in store.nodes.values() if n.props.get("kind") == "serial"}

    assert serial_keys() == {_key(FIRM_A, "DUP")}
    store.project_claim(NS, FIRM_A, PROG_A, facts(11, serial_claimed="OTHER"), None, [])
    assert store.components(NS, FIRM_A, PROG_A, a.claim_id, "same_serial") == {a.claim_id}
    assert serial_keys() == {_key(FIRM_A, "DUP"), _key(FIRM_A, "OTHER")}  # DUP is still a's
    store.project_claim(NS, FIRM_A, PROG_A, facts(10, serial_claimed="NEW"), None, [])
    assert serial_keys() == {_key(FIRM_A, "NEW"), _key(FIRM_A, "OTHER")}  # DUP had no link left: deleted


def _key(firm: uuid.UUID, serial: str) -> str:
    return identity_key(firm_key(SECRET, firm), "serial", serial)


def test_rings_cross_kinds_and_edges_name_the_kind() -> None:
    store = InMemoryGraphStore(SECRET)
    a = facts(20, address="9 Ring Rd")
    b = facts(21, address="9 RING RD.", payout_destination="shared")
    c = facts(22, payout_destination="shared")
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(x) for x in (a, b, c)])
    ids = {a.claim_id, b.claim_id, c.claim_id}
    assert store.ring(NS, FIRM_A, PROG_A, a.claim_id) == ids
    assert store.program_rings(NS, FIRM_A, PROG_A) == [frozenset(ids)]
    assert store.program_components(NS, FIRM_A, PROG_A, "same_address") == [frozenset({a.claim_id, b.claim_id})]
    edges = store.program_edges(NS, FIRM_A, PROG_A, ids, 10)
    assert {(frozenset({e.a, e.b}), e.kind) for e in edges} == {
        (frozenset({a.claim_id, b.claim_id}), "same_address"),
        (frozenset({b.claim_id, c.claim_id}), "same_payout"),
    }


def test_planted_cross_firm_relationships_do_not_join_components() -> None:
    store = InMemoryGraphStore(SECRET)
    a1, a2 = facts(30, serial_claimed="X1"), facts(31, serial_claimed="X1")
    b1, b2 = facts(32, serial_claimed="X1"), facts(33, serial_claimed="X1")
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(a1), ClaimProjection(a2)])
    store.project_claims(NS, FIRM_B, PROG_B, [ClaimProjection(b1), ClaimProjection(b2)])
    ua = store.claim_uid(NS, FIRM_A, PROG_A, a1.claim_id)
    ub = store.claim_uid(NS, FIRM_B, PROG_B, b1.claim_id)
    scope_a = {"ns": NS, "firm_id": str(FIRM_A), "program_id": str(PROG_A)}
    store.plant("SAME_SERIAL", ua, ub)  # no scope at all
    store.plant("SAME_SERIAL", ua, ub, scope_a)  # firm A's scope, pointing at firm B's claim
    assert store.components(NS, FIRM_A, PROG_A, a1.claim_id, "same_serial") == {a1.claim_id, a2.claim_id}
    assert store.components(NS, FIRM_B, PROG_B, b1.claim_id, "same_serial") == {b1.claim_id, b2.claim_id}
    assert store.ring(NS, FIRM_A, PROG_A, a1.claim_id) == {a1.claim_id, a2.claim_id}


def test_admin_purges_and_epoch_on_the_in_memory_store() -> None:
    store = InMemoryGraphStore(SECRET)
    a, b = facts(40, serial_claimed="P"), facts(41, serial_claimed="P")
    store.project_claims(NS, FIRM_A, PROG_A, [ClaimProjection(a), ClaimProjection(b)])
    store.project_claims(NS, FIRM_B, PROG_B, [ClaimProjection(facts(42))])
    assert graph_programs(store, NS) == {(FIRM_A, PROG_A), (FIRM_B, PROG_B)}
    assert purge_absent(store, NS, FIRM_A, PROG_A, [a.claim_id]) == 1
    assert store.components(NS, FIRM_A, PROG_A, a.claim_id, "same_serial") == {a.claim_id}
    assert purge_program(store, NS, FIRM_A, PROG_A) > 0
    assert purge_firm(store, NS, FIRM_B) > 0
    assert store.nodes == {}
    assert get_epoch(store, NS) is None
    set_epoch(store, NS, "e1")
    assert get_epoch(store, NS) == "e1"


# --- cluster rows -------------------------------------------------------------------------------------------------


def _row(n: int, members: set[int], age: int) -> ClusterRow:
    return ClusterRow(
        uuid.UUID(int=n),
        frozenset(uuid.UUID(int=m) for m in members),
        Decimal("1"),
        datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=age),
    )


def ids(*ns: int) -> frozenset[uuid.UUID]:
    return frozenset(uuid.UUID(int=n) for n in ns)


def test_plan_merge_keeps_the_oldest_row_and_split_keeps_it_for_the_largest_piece() -> None:
    merge = plan_cluster_rows([_row(2, {3, 4}, 5), _row(1, {1, 2}, 0)], [ids(1, 2, 3, 4)])
    assert [(r.id, p) for r, p in merge.keep] == [(uuid.UUID(int=1), ids(1, 2, 3, 4))]
    assert [r.id for r in merge.delete] == [uuid.UUID(int=2)] and merge.insert == []
    split = plan_cluster_rows([_row(7, {1, 2, 3, 4, 5}, 0)], [ids(1, 2, 3), ids(4, 5)])
    assert [(r.id, p) for r, p in split.keep] == [(uuid.UUID(int=7), ids(1, 2, 3))]
    assert split.insert == [ids(4, 5)] and split.delete == []
    gone = plan_cluster_rows([_row(9, {1, 2}, 0)], [ids(1), ids(2)])
    assert gone.keep == [] and gone.insert == [] and [r.id for r in gone.delete] == [uuid.UUID(int=9)]


def test_pair_score() -> None:
    assert pair_score(1, 2) == Decimal("1.0000")
    assert pair_score(2, 3) == Decimal("0.6667")
    assert pair_score(0, 1) == Decimal("1")


# --- statements ---------------------------------------------------------------------------------------------------


def test_every_tenant_statement_binds_the_scope_and_each_kind_uses_only_its_types() -> None:
    for stmt in TENANT_STATEMENTS:
        assert all(p in stmt for p in ("$ns", "$firm_id", "$program_id")), stmt
    rel_types = {"SAME_PHOTO", "SIMILAR_PHOTO", "SAME_ADDRESS", "SAME_SERIAL", "SAME_PAYOUT", "SAME_MAILING"}
    for kind in GRAPH_KINDS:
        own = {IDENTITY_REL[kind]} | ({"SIMILAR_PHOTO"} if kind == "same_photo" else set())
        for stmt in (COMPONENT[kind], PROGRAM_COMPONENTS[kind]):
            assert {t for t in rel_types if f"{t}|" in stmt or f"{t}*" in stmt or f":{t}" in stmt} == own
        assert {t for t in rel_types if f":{t} " in LINK_IDENTITY[kind]} == {IDENTITY_REL[kind]}
        assert "{ns: $ns, firm_id: $firm_id, program_id: $program_id}" in COMPONENT[kind]
    assert all(t in RING for t in rel_types)


# --- settings -----------------------------------------------------------------------------------------------------


def test_prod_settings_refuse_development_graph_values() -> None:
    with pytest.raises(ValidationError) as err:
        Settings(app_env="prod", portal_base_url="https://recallops.example/claim")
    message = str(err.value)
    for needle in ("NEO4J_NAMESPACE must be prod", "example value", "development password"):
        assert needle in message
    good: dict[str, Any] = {
        "app_env": "prod",
        "portal_base_url": "https://recallops.example/claim",
        "neo4j_namespace": "prod",
        "neo4j_uri": "neo4j+s://abc123.databases.neo4j.io",
        "neo4j_password": SecretStr("a-real-password"),
        "graph_key_secret": SecretStr("x" * 48),
    }
    assert Settings(**good).neo4j_namespace == "prod"
    with pytest.raises(ValidationError, match="must use TLS"):
        Settings(**{**good, "neo4j_uri": "neo4j://abc123.databases.neo4j.io"})
    with pytest.raises(ValidationError, match="shorter than 32 bytes"):
        Settings(**{**good, "graph_key_secret": SecretStr("short")})
    with pytest.raises(ValidationError, match="reserved for APP_ENV=prod"):
        Settings(app_env="dev", neo4j_namespace="prod")
    assert (
        Settings(app_env="prod", neo4j_uri="", portal_base_url="https://recallops.example/claim").neo4j_uri == ""
    )  # a disabled graph needs no graph values
    assert Settings(app_env="test").graph_key_secret.get_secret_value() == EXAMPLE_GRAPH_KEY_SECRET
    assert "graph-dev-only" not in repr(Settings(app_env="test"))


def test_disabled_graph_builds_no_store() -> None:
    settings = Settings(app_env="test", neo4j_uri="")
    assert build_graph_store(settings) is None
    assert graph_status(None) == "disabled"
    assert graph_status(InMemoryGraphStore.from_settings(settings)) == "ok"
