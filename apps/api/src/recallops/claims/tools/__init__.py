"""Pure-code adjudication tools (01-backend.md section 6, tools 3 to 8 without vision).

Every tool takes typed inputs and returns a typed result that cites the rule it used. They are shared by
the rules-only adjudicator (M1) and the agent graph (M2)."""

from recallops.claims.tools.checks import (
    PHASH_KINDS,
    TOOLS_VERSION,
    ClaimFacts,
    DestructionCheck,
    DuplicateHit,
    DuplicatesCheck,
    IdentityCheck,
    PhotoRef,
    PurchaseCheck,
    check_date_code,
    check_destruction_presence,
    check_duplicates,
    check_model,
    check_purchase,
    check_serial,
)

__all__ = [
    "PHASH_KINDS",
    "TOOLS_VERSION",
    "ClaimFacts",
    "DestructionCheck",
    "DuplicateHit",
    "DuplicatesCheck",
    "IdentityCheck",
    "PhotoRef",
    "PurchaseCheck",
    "check_date_code",
    "check_destruction_presence",
    "check_duplicates",
    "check_model",
    "check_purchase",
    "check_serial",
]
