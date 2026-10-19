"""Fraud graph CLI (a job: recallops_internal for discovery, the app role under RLS for each program's rewrite).

    python -m recallops.fraud sync --program <uuid>   rebuild one program from Postgres, purge absent claim nodes
    python -m recallops.fraud sync --all              every program; also purge programs Postgres no longer has
    python -m recallops.fraud status                  graph status, namespace, epochs, dirty claim count

Scoped to NEO4J_NAMESPACE. Refuses a NEO4J_URI that is not on localhost unless APP_ENV=prod.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Engine, text

from recallops.db.engine import app_engine, internal_engine
from recallops.fraud.graph import GraphError, build_graph_store, graph_status
from recallops.fraud.graph_admin import ensure_schema, get_epoch, graph_programs, purge_program
from recallops.fraud.sync import EPOCH_KEY_PREFIX, check_epoch, sync_program
from recallops.logs import configure
from recallops.settings import Settings, get_settings, is_local_uri


def _programs(engine: Engine, program: UUID | None) -> list[tuple[UUID, UUID]]:
    sql = "SELECT firm_id, id FROM recall_programs"
    params: dict[str, object] = {}
    if program is not None:
        sql += " WHERE id = :p"
        params["p"] = program
    with engine.connect() as conn:
        return [(r.firm_id, r.id) for r in conn.execute(text(sql + " ORDER BY firm_id, id"), params)]


def _guard_uri(settings: Settings) -> str | None:
    if settings.neo4j_uri and not is_local_uri(settings.neo4j_uri) and settings.app_env != "prod":
        return "refusing a non-localhost NEO4J_URI outside APP_ENV=prod"
    return None


def cmd_sync(settings: Settings, program: UUID | None) -> int:
    problem = _guard_uri(settings)
    if problem:
        print(json.dumps({"error": problem}))
        return 2
    store = build_graph_store(settings)
    if store is None:
        print(json.dumps({"error": "fraud graph disabled (NEO4J_URI is empty)"}))
        return 2
    internal, app = internal_engine(), app_engine()
    ns = settings.neo4j_namespace
    try:
        ensure_schema(store)
        reset = check_epoch(store, internal, settings)
        targets = _programs(internal, program)
        if program is not None and not targets:
            print(json.dumps({"error": f"program {program} not found"}))
            return 2
        for firm_id, program_id in targets:
            print(json.dumps(sync_program(store, internal, app, settings, firm_id, program_id)))
        purged: list[str] = []
        if program is None:
            for firm_id, program_id in sorted(graph_programs(store, ns) - set(targets), key=str):
                purge_program(store, ns, firm_id, program_id)
                purged.append(str(program_id))
        print(json.dumps({"namespace": ns, "programs": len(targets), "epoch_reset": reset, "purged_programs": purged}))
        return 0
    except GraphError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    finally:
        store.close()


def cmd_status(settings: Settings) -> int:
    store = build_graph_store(settings)
    status = graph_status(store)
    out: dict[str, object] = {"graph": status, "namespace": settings.neo4j_namespace}
    engine = internal_engine()
    with engine.connect() as conn:
        out["pg_epoch"] = conn.execute(
            text("SELECT value FROM graph_state WHERE key = :k"), {"k": EPOCH_KEY_PREFIX + settings.neo4j_namespace}
        ).scalar_one_or_none()
        out["dirty_claims"] = conn.execute(
            text(
                "SELECT count(*) FROM claims "
                "WHERE status <> 'draft' AND graph_synced_version IS DISTINCT FROM graph_version"
            )
        ).scalar_one()
    if store is not None:
        try:
            out["graph_epoch"] = get_epoch(store, settings.neo4j_namespace) if status == "ok" else None
        except GraphError as exc:
            out["graph"], out["error"] = "unavailable", str(exc)
        finally:
            store.close()
    print(json.dumps(out))
    return 1 if out["graph"] == "unavailable" else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m recallops.fraud", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sync = sub.add_parser("sync", help="rebuild the graph projection from Postgres")
    which = sync.add_mutually_exclusive_group(required=True)
    which.add_argument("--program", type=UUID, help="one program id")
    which.add_argument("--all", action="store_true", help="every program")
    sub.add_parser("status", help="graph status, epochs, and dirty claims")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure(settings.log_level)
    if args.cmd == "sync":
        return cmd_sync(settings, args.program)
    return cmd_status(settings)


if __name__ == "__main__":
    sys.exit(main())
