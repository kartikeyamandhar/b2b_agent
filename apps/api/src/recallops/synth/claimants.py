"""Fictional claimants. Emails only at RFC 2606 example domains, phones only in the reserved 555-0100 to
555-0199 block, street and city names from a fictional syllable generator, payout destinations as opaque
synth tokens. Nothing here can resolve to a real person."""

from __future__ import annotations

import random
from typing import Any

FIRST = (
    "Avery",
    "Jordan",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Rowan",
    "Sage",
    "Parker",
    "Reese",
    "Emerson",
    "Hayden",
    "Kendall",
    "Logan",
    "Skyler",
    "Taylor",
    "Dakota",
    "Finley",
    "Harper",
    "Jules",
)
SYLLABLES = (
    "bel",
    "cor",
    "dan",
    "fen",
    "gar",
    "hol",
    "lin",
    "mar",
    "nor",
    "pel",
    "quin",
    "ros",
    "sil",
    "tam",
    "vor",
    "wen",
    "yar",
    "zel",
)
STREET_TYPES = ("St", "Ave", "Rd", "Ln", "Way", "Ct")
STATES = ("CA", "TX", "NY", "FL", "IL", "OH", "WA", "GA", "NC", "AZ")
DOMAINS = ("example.com", "example.net", "example.org")


def _word(rng: random.Random, parts: int) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(parts)).capitalize()


def person(rng: random.Random, program_key: str, index: int) -> dict[str, Any]:
    first, last = rng.choice(FIRST), _word(rng, 2)
    return {
        "name": f"{first} {last}",
        "email": f"synth.{program_key}.{index}@{rng.choice(DOMAINS)}",
        "phone": f"({rng.randint(201, 989)}) 555-01{rng.randint(0, 99):02d}",
        "address_line1": f"{rng.randint(10, 9999)} {_word(rng, 2)} {rng.choice(STREET_TYPES)}",
        "city": f"{_word(rng, 2)} Falls",
        "state": rng.choice(STATES),
        "zip": f"{rng.randint(10000, 99999)}",
        "payout_destination": f"synth_dest_{rng.getrandbits(64):016x}",
        "synthetic": True,
    }
