"""Claims for the fraud graph tests: Postgres insertion helpers, the golden-claims loader, and the adversarial
fixture (each case is one way the graph could disagree with the intended duplicate semantics).

The expected components of the adversarial fixture are pre-registered in tests/db/test_fraud_sync.py, not here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from recallops.settings import REPO_ROOT

NS = uuid.UUID("f4a0d000-0000-4000-8000-000000000000")
GOLDEN = REPO_ROOT / "evals" / "adjudication" / "golden_claims.json"
T0 = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Photo:
    kind: str
    sha256: str
    phash: str
    uploaded_at: datetime = T0


@dataclass(frozen=True)
class ClaimRow:
    id: UUID
    status: str = "submitted"
    claimant: dict[str, Any] = field(default_factory=dict)
    model_claimed: str | None = None
    serial_claimed: str | None = None
    retailer_claimed: str | None = None
    photos: tuple[Photo, ...] = ()


def cid(name: str) -> UUID:
    return uuid.uuid5(NS, f"claim:{name}")


def sha(name: str) -> str:
    return hashlib.sha256(f"photo-bytes:{name}".encode()).hexdigest()


def phash(name: str) -> str:
    """A deterministic 64-bit value; two different names are about 32 bits apart."""
    return hashlib.sha256(f"phash:{name}".encode()).hexdigest()[:16]


def flip(value: str, mask: int) -> str:
    return f"{int(value, 16) ^ mask:016x}"


def person(n: int, **overrides: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": f"Person {n}",
        "email": f"person{n}@example.org",
        "address_line1": f"{n} Unique Rd",
        "city": "Testville",
        "state": "CA",
        "zip": f"9{n:04d}",
        "payout_destination": f"synth_dest_{n:04d}",
        "synthetic": True,
    }
    base.update(overrides)
    return base


def insert_firm(conn: Connection, firm_id: UUID, name: str, program_ids: Sequence[UUID]) -> None:
    conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, :n)"), {"f": firm_id, "n": name})
    for pid in program_ids:
        conn.execute(
            text("INSERT INTO recall_programs (id, firm_id, value_threshold_cents) VALUES (:p, :f, 10000)"),
            {"p": pid, "f": firm_id},
        )


def insert_claims(conn: Connection, firm_id: UUID, program_id: UUID, claims: Sequence[ClaimRow]) -> None:
    if not claims:
        return
    conn.execute(
        text(
            "INSERT INTO claims (id, firm_id, program_id, token_hash, status, claimant, model_claimed, serial_claimed, "
            "retailer_claimed, submitted_at) VALUES (:id, :f, :p, :tok, :status, CAST(:claimant AS jsonb), :model, "
            ":serial, :retailer, CASE WHEN :status = 'draft' THEN NULL ELSE now() END)"
        ),
        [
            {
                "id": c.id,
                "f": firm_id,
                "p": program_id,
                "tok": f"tok-{c.id.hex}",
                "status": c.status,
                "claimant": json.dumps(c.claimant),
                "model": c.model_claimed,
                "serial": c.serial_claimed,
                "retailer": c.retailer_claimed,
            }
            for c in claims
        ],
    )
    photos = [(c, p) for c in claims for p in c.photos]
    if photos:
        add_photos(conn, firm_id, photos)


def add_photos(conn: Connection, firm_id: UUID, photos: Sequence[tuple[ClaimRow, Photo]]) -> None:
    conn.execute(
        text(
            "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, uploaded_at) "
            "VALUES (:f, :c, :kind, :path, :sha, :phash, :at)"
        ),
        [
            {
                "f": firm_id,
                "c": c.id,
                "kind": p.kind,
                "path": f"{c.id}/{p.kind}-{p.sha256[:8]}.jpg",
                "sha": p.sha256,
                "phash": p.phash,
                "at": p.uploaded_at,
            }
            for c, p in photos
        ],
    )


def golden_claims() -> dict[str, list[ClaimRow]]:
    """evals/adjudication/golden_claims.json by program key, as submitted claims (one program per golden key)."""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    out: dict[str, list[ClaimRow]] = {}
    for r in data["claims"]:
        out.setdefault(str(r["program"]), []).append(
            ClaimRow(
                id=UUID(r["claim_id"]),
                claimant=r.get("claimant") or {},
                model_claimed=r.get("model_claimed"),
                serial_claimed=r.get("serial_claimed"),
                retailer_claimed=r.get("retailer_claimed"),
                photos=tuple(Photo(p["kind"], p["sha256"], p["phash"]) for p in r.get("photos", [])),
            )
        )
    return out


def label(name: str, value: str | None = None, sha_name: str | None = None) -> Photo:
    return Photo("label", sha(sha_name or name), value or phash(name))


# The chain: chain1 -> chain2 is 4 bits, chain2 -> chain3 another 4 bits elsewhere, chain1 -> chain3 is 8 bits (> 6).
_CHAIN_BASE = phash("chain-base")
_CHAIN = (_CHAIN_BASE, flip(_CHAIN_BASE, 0xF), flip(_CHAIN_BASE, 0xFF))


def adversarial_claims() -> list[ClaimRow]:
    """Every claim has unique identities except where a case says otherwise. Names are the test's vocabulary."""
    claims = [
        # 1. pHash chain on label photos (distinct bytes).
        ClaimRow(cid("chain1"), claimant=person(1), serial_claimed="SER0001", photos=(label("chain1", _CHAIN[0]),)),
        ClaimRow(cid("chain2"), claimant=person(2), serial_claimed="SER0002", photos=(label("chain2", _CHAIN[1]),)),
        ClaimRow(cid("chain3"), claimant=person(3), serial_claimed="SER0003", photos=(label("chain3", _CHAIN[2]),)),
        # 2. Byte-identical photos under different photo kinds.
        ClaimRow(
            cid("kind_a"),
            claimant=person(4),
            serial_claimed="SER0004",
            photos=(Photo("label", sha("shared-bytes"), phash("shared-bytes")),),
        ),
        ClaimRow(
            cid("kind_b"),
            claimant=person(5),
            serial_claimed="SER0005",
            photos=(label("kind_b"), Photo("destruction", sha("shared-bytes"), phash("shared-bytes"))),
        ),
        # 3. A duplicate label photo that reup_b later replaces (needs_info re-upload).
        ClaimRow(cid("reup_a"), claimant=person(6), serial_claimed="SER0006", photos=(label("reup-shared"),)),
        ClaimRow(
            cid("reup_b"),
            status="needs_info",
            claimant=person(7),
            serial_claimed="SER0007",
            photos=(label("reup-shared"),),
        ),
        # 4. A draft that duplicates a submitted claim's serial, address, email, payout destination, and photo.
        ClaimRow(cid("draft_orig"), claimant=person(8), serial_claimed="SER0008", photos=(label("draft-shared"),)),
        ClaimRow(
            cid("draft_dup"),
            status="draft",
            claimant=person(8, name="Person 8 copy"),
            serial_claimed="SER0008",
            photos=(label("draft-shared"),),
        ),
        # 5. Serials that normalize to an empty string.
        ClaimRow(cid("dash1"), claimant=person(9), serial_claimed="-", photos=(label("dash1"),)),
        ClaimRow(cid("dash2"), claimant=person(10), serial_claimed=" - ", photos=(label("dash2"),)),
        # 6. Same email (case and spacing differ), different addresses.
        ClaimRow(
            cid("email1"),
            claimant=person(11, email="Pat.Doe@Example.org"),
            serial_claimed="SER0011",
            photos=(label("email1"),),
        ),
        ClaimRow(
            cid("email2"),
            claimant=person(12, email=" pat.doe@example.org"),
            serial_claimed="SER0012",
            photos=(label("email2"),),
        ),
        # 7. A ring across kinds: ring_a and ring_b share an address, ring_b and ring_c a payout destination.
        ClaimRow(
            cid("ring_a"),
            claimant=person(13, address_line1="77 Ring Rd", city="Loopton", zip="90077"),
            serial_claimed="SER0013",
            photos=(label("ring_a"),),
        ),
        ClaimRow(
            cid("ring_b"),
            claimant=person(
                14, address_line1="77 RING RD.", city="loopton", zip="90077", payout_destination="synth_dest_ring"
            ),
            serial_claimed="SER0014",
            photos=(label("ring_b"),),
        ),
        ClaimRow(
            cid("ring_c"),
            claimant=person(15, payout_destination="synth_dest_ring"),
            serial_claimed="SER0015",
            photos=(label("ring_c"),),
        ),
        # 8. A shared post-approval mailing address (portal payout route), formatted differently.
        ClaimRow(
            cid("mail1"),
            claimant={**person(16), "mailing_address": "1 Main St, Springfield IL 62701"},
            serial_claimed="SER0016",
            photos=(label("mail1"),),
        ),
        ClaimRow(
            cid("mail2"),
            claimant={**person(17), "mailing_address": "1 MAIN ST SPRINGFIELD, IL 62701"},
            serial_claimed="SER0017",
            photos=(label("mail2"),),
        ),
    ]
    return claims


def reupload(claim: ClaimRow) -> tuple[ClaimRow, Photo]:
    """A later label upload with new bytes and an unrelated pHash (facts use the latest photo per kind)."""
    return claim, Photo("label", sha(f"{claim.id}-reupload"), phash(f"{claim.id}-reupload"), T0 + timedelta(days=1))
