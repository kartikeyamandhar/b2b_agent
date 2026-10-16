"""Write the OpenAPI document the team codes against: python -m recallops.api.export_openapi <path>."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def document() -> dict[str, Any]:
    from recallops.api.app import app

    spec: dict[str, Any] = app.openapi()
    return spec


def render() -> str:
    return json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    target = Path(args[0]) if args else Path("docs/contracts/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target} ({len(document()['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
