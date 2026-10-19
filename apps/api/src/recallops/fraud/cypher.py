"""Every Cypher statement the fraud graph runs, as module constants (decisions log 2026-10-18, plan WP1 Revision 1).

Rules that keep the graph tenant-safe by construction:

- Nothing here is built from input at call time. Per-kind variants are expanded once, at import, from the closed
  table _REL_TYPES below; relationship types never come from a parameter. Call sites pass a constant (or a
  subscript of a constant mapping keyed by the GraphKind literal) as the first argument of tx.run; an AST
  meta-test enforces it.
- Every node and relationship carries ns, firm_id, and program_id (strings). Claim and Identity uids are built
  inside Cypher from bound parameters: ns:firm:program:claim and ns:firm:program:<identity kind>:<key>.
- Every traversal anchors on a unique-index seek, constrains every hop with the {ns, firm_id, program_id}
  property map, uses only the relationship types of one kind (or of all kinds for a ring), and filters the
  endpoints on the same three properties. With DISTINCT endpoints the planner keeps VarLengthExpand(Pruning,BFS).
- Statements used by graph.py (tenant reads and writes) all bind $ns and $firm_id. Schema, epoch, and purge
  statements are used by graph_admin.py only, which the API never imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

GraphKind = Literal["same_photo", "same_address", "same_serial", "same_payout", "same_mailing"]
GRAPH_KINDS: Final[tuple[GraphKind, ...]] = (
    "same_photo",
    "same_address",
    "same_serial",
    "same_payout",
    "same_mailing",
)

# Relationship types per duplicate kind. Claim-to-Identity links per kind, plus the claim-to-claim SIMILAR_PHOTO
# edge (pHash within the duplicate threshold, same photo kind) for same_photo.
_REL_TYPES: Final[Mapping[GraphKind, tuple[str, ...]]] = MappingProxyType(
    {
        "same_photo": ("SAME_PHOTO", "SIMILAR_PHOTO"),
        "same_address": ("SAME_ADDRESS",),
        "same_serial": ("SAME_SERIAL",),
        "same_payout": ("SAME_PAYOUT",),
        "same_mailing": ("SAME_MAILING",),
    }
)
IDENTITY_REL: Final[Mapping[GraphKind, str]] = MappingProxyType({k: v[0] for k, v in _REL_TYPES.items()})
RING_REL_TYPES: Final[tuple[str, ...]] = tuple(t for kind in GRAPH_KINDS for t in _REL_TYPES[kind])
REL_KIND: Final[Mapping[str, GraphKind]] = MappingProxyType({t: kind for kind in GRAPH_KINDS for t in _REL_TYPES[kind]})

_SCOPE: Final = "{ns: $ns, firm_id: $firm_id, program_id: $program_id}"
_PREFIX: Final = "$ns + ':' + $firm_id + ':' + $program_id + ':'"


def _in_scope(var: str) -> str:
    return f"{var}.ns = $ns AND {var}.firm_id = $firm_id AND {var}.program_id = $program_id"


def _expand(template: str, **parts: str) -> str:
    """Import-time expansion of a template with fixed fragments from this module (never call-time input)."""
    out = template
    for name, value in parts.items():
        out = out.replace(f"<{name}>", value)
    if re.search(r"<[A-Z_]+>", out):
        raise ValueError(f"unexpanded fragment in Cypher template: {out}")
    return out.strip()


# ---------------------------------------------------------------------------------------------------------------
# Tenant statements (graph.py). Each binds $ns, $firm_id, $program_id.

DELETE_CLAIM_RELS: Final[str] = _expand(
    """
UNWIND $claim_ids AS cid
MATCH (c:Claim {uid: <PREFIX> + cid})
MATCH (c)-[r]-(x)
WITH r, CASE WHEN x:Identity THEN x.uid END AS identity_uid
DELETE r
RETURN DISTINCT identity_uid
""",
    PREFIX=_PREFIX,
)

DELETE_ORPHAN_IDENTITIES: Final[str] = _expand(
    """
UNWIND $identity_uids AS u
MATCH (i:Identity {uid: u})
WHERE <SCOPED_I> AND NOT (i)--()
DELETE i
""",
    SCOPED_I=_in_scope("i"),
)

MERGE_CLAIMS: Final[str] = _expand(
    """
UNWIND $claim_ids AS cid
MERGE (c:Claim {uid: <PREFIX> + cid})
SET c.ns = $ns, c.firm_id = $firm_id, c.program_id = $program_id, c.claim_id = cid
""",
    PREFIX=_PREFIX,
)

_LINK_IDENTITY = """
UNWIND $links AS l
MATCH (c:Claim {uid: <PREFIX> + l.claim_id})
WHERE <SCOPED_C>
MERGE (i:Identity {uid: <PREFIX> + l.identity_kind + ':' + l.key})
SET i.ns = $ns, i.firm_id = $firm_id, i.program_id = $program_id, i.kind = l.identity_kind, i.key = l.key
MERGE (c)-[:<REL> <SCOPE>]->(i)
"""
LINK_IDENTITY: Final[Mapping[GraphKind, str]] = MappingProxyType(
    {
        kind: _expand(_LINK_IDENTITY, PREFIX=_PREFIX, SCOPED_C=_in_scope("c"), REL=IDENTITY_REL[kind], SCOPE=_SCOPE)
        for kind in GRAPH_KINDS
    }
)

LINK_SIMILAR: Final[str] = _expand(
    """
UNWIND $pairs AS p
MATCH (c:Claim {uid: <PREFIX> + p.claim_id})
MATCH (o:Claim {uid: <PREFIX> + p.other_id})
WHERE <SCOPED_C> AND <SCOPED_O> AND c <> o
MERGE (c)-[r:SIMILAR_PHOTO {ns: $ns, firm_id: $firm_id, program_id: $program_id, photo_kind: p.photo_kind}]-(o)
SET r.bits = p.bits
""",
    PREFIX=_PREFIX,
    SCOPED_C=_in_scope("c"),
    SCOPED_O=_in_scope("o"),
)

_COMPONENT = """
MATCH (c:Claim {uid: <PREFIX> + $claim_id})
MATCH (c)-[:<RELS>* <SCOPE>]-(o)
WITH DISTINCT o
WHERE o:Claim AND <SCOPED_O>
RETURN o.claim_id AS claim_id
"""
COMPONENT: Final[Mapping[GraphKind, str]] = MappingProxyType(
    {
        kind: _expand(
            _COMPONENT, PREFIX=_PREFIX, RELS="|".join(_REL_TYPES[kind]), SCOPE=_SCOPE, SCOPED_O=_in_scope("o")
        )
        for kind in GRAPH_KINDS
    }
)
RING: Final[str] = _expand(
    _COMPONENT, PREFIX=_PREFIX, RELS="|".join(RING_REL_TYPES), SCOPE=_SCOPE, SCOPED_O=_in_scope("o")
)

# One row per linked claim: the smallest claim id it reaches (itself included) is the component's root.
_PROGRAM_COMPONENTS = """
MATCH (c:Claim)
WHERE <SCOPED_C>
CALL (c) {
  MATCH (c)-[:<RELS>* <SCOPE>]-(o)
  WITH DISTINCT o
  WHERE o:Claim AND <SCOPED_O>
  RETURN min(o.claim_id) AS reached
}
WITH c, reached
WHERE reached IS NOT NULL
RETURN c.claim_id AS claim_id, CASE WHEN reached < c.claim_id THEN reached ELSE c.claim_id END AS root
"""
PROGRAM_COMPONENTS: Final[Mapping[GraphKind, str]] = MappingProxyType(
    {
        kind: _expand(
            _PROGRAM_COMPONENTS,
            SCOPED_C=_in_scope("c"),
            RELS="|".join(_REL_TYPES[kind]),
            SCOPE=_SCOPE,
            SCOPED_O=_in_scope("o"),
        )
        for kind in GRAPH_KINDS
    }
)
PROGRAM_RINGS: Final[str] = _expand(
    _PROGRAM_COMPONENTS,
    SCOPED_C=_in_scope("c"),
    RELS="|".join(RING_REL_TYPES),
    SCOPE=_SCOPE,
    SCOPED_O=_in_scope("o"),
)

# Direct claim pairs among a given set of claims: through a shared Identity of one relationship type, or a
# SIMILAR_PHOTO edge. The relationship type is returned, never taken as input.
PAIRS_VIA_IDENTITY: Final[str] = _expand(
    """
UNWIND $claim_ids AS cid
MATCH (a:Claim {uid: <PREFIX> + cid})
MATCH (a)-[r1:<IDENTITY_RELS> <SCOPE>]->(i:Identity)<-[r2 <SCOPE>]-(b:Claim)
WHERE type(r2) = type(r1) AND a.claim_id < b.claim_id AND b.claim_id IN $claim_ids
  AND <SCOPED_I> AND <SCOPED_B>
RETURN DISTINCT a.claim_id AS a, b.claim_id AS b, type(r1) AS rel
ORDER BY a, b, rel
LIMIT $limit
""",
    PREFIX=_PREFIX,
    IDENTITY_RELS="|".join(IDENTITY_REL[k] for k in GRAPH_KINDS),
    SCOPE=_SCOPE,
    SCOPED_I=_in_scope("i"),
    SCOPED_B=_in_scope("b"),
)
PAIRS_VIA_SIMILAR: Final[str] = _expand(
    """
UNWIND $claim_ids AS cid
MATCH (a:Claim {uid: <PREFIX> + cid})
MATCH (a)-[r:SIMILAR_PHOTO <SCOPE>]-(b:Claim)
WHERE a.claim_id < b.claim_id AND b.claim_id IN $claim_ids AND <SCOPED_B>
RETURN DISTINCT a.claim_id AS a, b.claim_id AS b, type(r) AS rel
ORDER BY a, b
LIMIT $limit
""",
    PREFIX=_PREFIX,
    SCOPE=_SCOPE,
    SCOPED_B=_in_scope("b"),
)

TENANT_STATEMENTS: Final[tuple[str, ...]] = (
    DELETE_CLAIM_RELS,
    DELETE_ORPHAN_IDENTITIES,
    MERGE_CLAIMS,
    *LINK_IDENTITY.values(),
    LINK_SIMILAR,
    *COMPONENT.values(),
    RING,
    *PROGRAM_COMPONENTS.values(),
    PROGRAM_RINGS,
    PAIRS_VIA_IDENTITY,
    PAIRS_VIA_SIMILAR,
)

# ---------------------------------------------------------------------------------------------------------------
# Admin statements (graph_admin.py only): schema, epoch, retention.

SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    "CREATE CONSTRAINT recallops_claim_uid IF NOT EXISTS FOR (c:Claim) REQUIRE c.uid IS UNIQUE",
    "CREATE CONSTRAINT recallops_identity_uid IF NOT EXISTS FOR (i:Identity) REQUIRE i.uid IS UNIQUE",
    "CREATE CONSTRAINT recallops_graphmeta_uid IF NOT EXISTS FOR (m:GraphMeta) REQUIRE m.uid IS UNIQUE",
    "CREATE INDEX recallops_claim_scope IF NOT EXISTS FOR (c:Claim) ON (c.ns, c.firm_id, c.program_id)",
    "CREATE INDEX recallops_identity_scope IF NOT EXISTS FOR (i:Identity) ON (i.ns, i.firm_id, i.program_id)",
)

GET_EPOCH: Final[str] = "MATCH (m:GraphMeta {uid: $ns}) RETURN m.epoch AS epoch"
SET_EPOCH: Final[str] = "MERGE (m:GraphMeta {uid: $ns}) SET m.ns = $ns, m.epoch = $epoch, m.updated_at = datetime()"

PURGE_PROGRAM_CLAIMS: Final[str] = _expand(
    "MATCH (c:Claim) WHERE <SCOPED_C> DETACH DELETE c RETURN count(*) AS n", SCOPED_C=_in_scope("c")
)
PURGE_PROGRAM_IDENTITIES: Final[str] = _expand(
    "MATCH (i:Identity) WHERE <SCOPED_I> DETACH DELETE i RETURN count(*) AS n", SCOPED_I=_in_scope("i")
)
PURGE_FIRM_CLAIMS: Final[str] = (
    "MATCH (c:Claim) WHERE c.ns = $ns AND c.firm_id = $firm_id DETACH DELETE c RETURN count(*) AS n"
)
PURGE_FIRM_IDENTITIES: Final[str] = (
    "MATCH (i:Identity) WHERE i.ns = $ns AND i.firm_id = $firm_id DETACH DELETE i RETURN count(*) AS n"
)
PURGE_ABSENT_CLAIMS: Final[str] = _expand(
    "MATCH (c:Claim) WHERE <SCOPED_C> AND NOT c.claim_id IN $keep DETACH DELETE c RETURN count(*) AS n",
    SCOPED_C=_in_scope("c"),
)
PURGE_PROGRAM_ORPHANS: Final[str] = _expand(
    "MATCH (i:Identity) WHERE <SCOPED_I> AND NOT (i)--() DELETE i RETURN count(*) AS n", SCOPED_I=_in_scope("i")
)
PURGE_NAMESPACE: Final[str] = "MATCH (n) WHERE n.ns = $ns DETACH DELETE n RETURN count(*) AS n"
GRAPH_PROGRAMS: Final[str] = (
    "MATCH (c:Claim) WHERE c.ns = $ns RETURN DISTINCT c.firm_id AS firm_id, c.program_id AS program_id "
    "UNION MATCH (i:Identity) WHERE i.ns = $ns RETURN DISTINCT i.firm_id AS firm_id, i.program_id AS program_id"
)
