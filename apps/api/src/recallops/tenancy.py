"""FirmContext: the only way tenant code learns which firm it serves.

Built by the AuthProvider from a verified identity (API), or by a job from an explicit, validated
--firm argument (CLI). Never built from a request body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

Role = Literal["admin", "reviewer"]


@dataclass(frozen=True, slots=True)
class FirmContext:
    firm_id: UUID
    user_id: str
    role: Role
