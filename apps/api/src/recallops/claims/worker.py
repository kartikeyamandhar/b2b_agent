"""Polling worker: python -m recallops.claims.worker (make worker).

One process at a time (a Postgres advisory lock held for the process's life), which also serializes claims per
firm and model calls across the worker (decisions log 2026-10-18). Each tick does one unit of work:

1. A reviewer resume: an agent_runs row waiting for a person with resume_payload set is claimed atomically (lease
   on the run), resumed with Command(resume=payload) on its own checkpoint thread, then marked done with the
   payload cleared in the same statement.
2. A submitted claim: leased atomically (lease_until, attempts + 1). If the claim already has a running agent run
   whose checkpoint has a next node (a crash), the run is attached and continued with invoke(None); otherwise a new
   run and thread start. After invoke returns, the worker (never the graph) marks a paused run waiting_human,
   capped, or failed, so a resume is never possible before the interrupt is persisted (durability sync).

A claim that fails WORKER_MAX_ATTEMPTS times is held for a person (status capped). BudgetExhausted stops the
worker: the global model budget is spent.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.billing.fake import AllowAllBilling
from recallops.billing.interface import BillingGate
from recallops.claims.agent_tools import ToolContext
from recallops.claims.context import LoadedClaim, load_claim, photo_reader, program_claims
from recallops.claims.effects import DbEffects
from recallops.claims.graph import VARIANTS, AgentConfig, build_graph, run_budget, summarize
from recallops.claims.rules_only import adjudicate as rules_only_adjudicate
from recallops.db.engine import internal_engine
from recallops.llm.client import LlmClient, build_client
from recallops.llm.meter import BudgetExhausted
from recallops.logs import configure, get_logger, log_event
from recallops.models import UsageMeter, WorkerHeartbeat
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.ledger import PayoutLedger
from recallops.retrieval.embed import build_embedder
from recallops.retrieval.search import PgRetriever
from recallops.settings import Settings, get_settings
from recallops.tenancy import FirmContext
from recallops.tracing.writer import DbTrace

log = get_logger("claims.worker")

LOCK_KEY = 7_420_017  # pg advisory lock id for "the one worker"


class AnotherWorkerRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class Leased:
    claim_id: UUID
    firm_id: UUID
    attempts: int


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@contextmanager
def checkpointer(settings: Settings) -> Iterator[PostgresSaver]:
    """Autocommit connection with search_path on the langgraph schema (migration 0002)."""
    conn = psycopg.Connection.connect(
        libpq(settings.database_url_internal),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=langgraph,public",
    )
    try:
        yield PostgresSaver(conn)
    finally:
        conn.close()


class Worker:
    def __init__(
        self,
        engine: Engine | None = None,
        settings: Settings | None = None,
        llm: LlmClient | None = None,
        billing: BillingGate | None = None,
        ledger: PayoutLedger | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or internal_engine()
        self.billing = billing or AllowAllBilling()
        self.ledger = ledger or DbPayoutLedger(self.engine)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._llm = llm
        self.config: AgentConfig = VARIANTS[self.settings.agent_variant]
        self.embedder = build_embedder(self.settings.embedding_model_id, self.settings.embedding_dim)
        self.stopped = False

    @property
    def llm(self) -> LlmClient:
        if self._llm is None:
            self._llm = build_client(self.settings, record_dir=self.settings.llm_record_dir)
        return self._llm

    # leases

    def heartbeat(self, info: dict[str, Any] | None = None) -> None:
        with Session(self.engine) as session, session.begin():
            stmt = insert(WorkerHeartbeat).values(worker_id=self.worker_id, seen_at=datetime.now(UTC), info=info or {})
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["worker_id"], set_={"seen_at": stmt.excluded.seen_at, "info": stmt.excluded.info}
                )
            )

    def lease_claim(self) -> Leased | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE claims SET lease_owner = :me, lease_until = now() + make_interval(secs => :ttl), "
                    "attempts = attempts + 1 WHERE id = (SELECT id FROM claims WHERE status = 'submitted' "
                    "AND (lease_until IS NULL OR lease_until < now()) ORDER BY submitted_at NULLS LAST, id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id, firm_id, attempts"
                ),
                {"me": self.worker_id, "ttl": self.settings.worker_lease_s},
            ).first()
        return Leased(row.id, row.firm_id, row.attempts) if row else None

    def release_claim(self, claim_id: UUID, undo_attempt: bool = False) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE claims SET lease_owner = NULL, lease_until = NULL"
                    + (", attempts = greatest(attempts - 1, 0)" if undo_attempt else "")
                    + " WHERE id = :c AND lease_owner = :me"
                ),
                {"c": claim_id, "me": self.worker_id},
            )

    def lease_resume(self) -> tuple[UUID, UUID, dict[str, Any]] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE agent_runs SET status = 'running', lease_owner = :me, "
                    "lease_until = now() + make_interval(secs => :ttl) WHERE id = (SELECT id FROM agent_runs "
                    "WHERE graph = 'adjudicate' AND resume_payload IS NOT NULL "
                    "AND status IN ('waiting_human', 'capped', 'failed') "
                    "AND (lease_until IS NULL OR lease_until < now()) ORDER BY started_at, id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id, claim_id, resume_payload"
                ),
                {"me": self.worker_id, "ttl": self.settings.worker_lease_s},
            ).first()
        return (row.id, row.claim_id, dict(row.resume_payload)) if row else None

    # running

    def _ctx(self, loaded: LoadedClaim, session: Session, retrieval_on: bool) -> ToolContext:
        def others() -> list[Any]:
            with Session(self.engine) as s:
                return program_claims(s, loaded.firm_id, loaded.program_id, loaded.claim_id)

        retriever = PgRetriever(session, loaded.firm_id, self.embedder, self.config.retrieval) if retrieval_on else None
        return ToolContext(
            claim=loaded.facts,
            rules=loaded.rules,
            program_id=loaded.program_id,
            value_threshold_cents=loaded.value_threshold_cents,
            others=others,
            photo_bytes=photo_reader(self.settings.storage_dir, loaded.photo_paths),
            retriever=retriever,
            llm=self.llm,
            phash_bits=self.settings.phash_duplicate_bits,
        )

    def _effects(self, loaded: LoadedClaim, lease_owner: str | None) -> DbEffects:
        ctx = FirmContext(firm_id=loaded.firm_id, user_id="agent", role="admin")
        return DbEffects(self.engine, ctx, loaded.claim_id, loaded.refund_cents, self.ledger, self.billing, lease_owner)

    def _existing_run(self, claim_id: UUID) -> UUID | None:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT id FROM agent_runs WHERE claim_id = :c AND graph = 'adjudicate' AND status = 'running' "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"c": claim_id},
            ).scalar_one_or_none()

    def _record_usage(self, firm_id: UUID, cost: Decimal) -> None:
        period = datetime.now(UTC).date().replace(day=1)
        with Session(self.engine) as session, session.begin():
            stmt = insert(UsageMeter).values(
                firm_id=firm_id, period_start=period, claims_adjudicated=1, model_cost_usd=cost
            )
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["firm_id", "period_start"],
                    set_={
                        "claims_adjudicated": UsageMeter.claims_adjudicated + 1,
                        "model_cost_usd": UsageMeter.model_cost_usd + cost,
                    },
                )
            )
        self.billing.record_usage(FirmContext(firm_id=firm_id, user_id="agent", role="admin"), 1, cost)

    def adjudicate(self, leased: Leased) -> str:
        with Session(self.engine) as session:
            loaded = load_claim(session, leased.claim_id)
        if self.settings.adjudicator == "rules_only":
            return self._rules_only(loaded)
        attached = self._existing_run(loaded.claim_id)
        trace = (
            DbTrace.attach(self.engine, attached)
            if attached
            else DbTrace(
                self.engine,
                "adjudicate",
                firm_id=loaded.firm_id,
                claim_id=loaded.claim_id,
                program_id=loaded.program_id,
                model_id=self.settings.llm_model_id,
                config=self.config.as_dict(),
            )
        )
        budget = run_budget(self.settings, trace.snapshot())
        with checkpointer(self.settings) as saver, Session(self.engine) as rsession:
            tools = self._ctx(loaded, rsession, self.config.retrieval != "none")
            graph = build_graph(
                tools,
                self.llm,
                trace,
                budget,
                self.config,
                self._effects(loaded, self.worker_id),
                human_in_loop=True,
                checkpointer=saver,
            )
            config = {"configurable": {"thread_id": str(trace.run_id)}}
            pending = attached is not None and bool(graph.get_state(config).next)
            log_event(
                log,
                "worker_run_start",
                claim_id=str(loaded.claim_id),
                run_id=str(trace.run_id),
                firm_id=str(loaded.firm_id),
                resumed=pending,
                attempt=leased.attempts,
            )
            values = graph.invoke(None if pending else {}, config, durability="sync")
        result = summarize(values)
        if result.interrupted:
            held = result.held or {}
            trace.set_status(held.get("run_status", "waiting_human"), outcome={"held": held.get("reason")})
        else:
            trace.finish("done", {"outcome": result.outcome, "routed_to_human": result.routed_to_human})
        self.release_claim(loaded.claim_id)
        self._record_usage(loaded.firm_id, charged_cost(self.engine, trace.run_id))
        return result.outcome or (result.held or {}).get("reason", "held")

    def _rules_only(self, loaded: LoadedClaim) -> str:
        with Session(self.engine) as session:
            others = program_claims(session, loaded.firm_id, loaded.program_id, loaded.claim_id)
        trace = DbTrace(self.engine, "rules_only", loaded.firm_id, loaded.claim_id, loaded.program_id)
        result = rules_only_adjudicate(
            loaded.facts, loaded.rules, others, loaded.value_threshold_cents, trace, self.settings.phash_duplicate_bits
        )
        effects = self._effects(loaded, self.worker_id)
        decision = {
            "outcome": result.outcome,
            "reasons": result.reasons,
            "cited_rules": result.cited_rules,
            "question_for_claimant": ("Please provide: " + "; ".join(result.reasons) + ".")
            if result.outcome == "needs_info"
            else None,
            "confidence": None,
            "decided_by": "rules_only",
        }
        if result.routed_to_human:
            effects.hold(
                {
                    "reason": "flagged" if result.outcome == "flag" else "over_value_threshold",
                    "decision": decision,
                    "claim_status": "flagged" if result.outcome == "flag" else "under_review",
                }
            )
        else:
            effects.apply(decision)
        self.release_claim(loaded.claim_id)
        self._record_usage(loaded.firm_id, Decimal(0))
        return result.outcome

    def resume(self, run_id: UUID, claim_id: UUID, payload: dict[str, Any]) -> str:
        with Session(self.engine) as session:
            loaded = load_claim(session, claim_id)
        trace = DbTrace.attach(self.engine, run_id)
        budget = run_budget(self.settings, trace.snapshot())
        with checkpointer(self.settings) as saver, Session(self.engine) as rsession:
            tools = self._ctx(loaded, rsession, self.config.retrieval != "none")
            graph = build_graph(
                tools,
                self.llm,
                trace,
                budget,
                VARIANTS[self._run_variant(run_id)],
                self._effects(loaded, None),
                human_in_loop=True,
                checkpointer=saver,
            )
            values = graph.invoke(
                Command(resume=payload), {"configurable": {"thread_id": str(run_id)}}, durability="sync"
            )
        result = summarize(values)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE agent_runs SET status = 'done', resume_payload = NULL, lease_owner = NULL, "
                    "lease_until = NULL, finished_at = now(), outcome = CAST(:o AS jsonb) WHERE id = :r"
                ),
                {"r": run_id, "o": json.dumps({"outcome": result.outcome, "reviewed": True})},
            )
        log_event(log, "worker_resume_done", run_id=str(run_id), claim_id=str(claim_id), outcome=result.outcome)
        return result.outcome or "reviewed"

    def _run_variant(self, run_id: UUID) -> str:
        with self.engine.connect() as conn:
            config = conn.execute(text("SELECT config FROM agent_runs WHERE id = :r"), {"r": run_id}).scalar_one()
        return str((config or {}).get("variant", self.config.variant))

    def hold_exhausted(self, leased: Leased) -> None:
        with Session(self.engine) as session:
            loaded = load_claim(session, leased.claim_id)
        self._effects(loaded, self.worker_id).hold(
            {
                "reason": "failed",
                "detail": f"{leased.attempts - 1} failed attempts",
                "decision": None,
                "claim_status": "capped",
            }
        )
        self.release_claim(leased.claim_id)

    def tick(self) -> bool:
        """One unit of work. True when something was done."""
        resume = self.lease_resume()
        if resume is not None:
            self.resume(*resume)
            return True
        leased = self.lease_claim()
        if leased is None:
            return False
        if leased.attempts > self.settings.worker_max_attempts:
            self.hold_exhausted(leased)
            return True
        ctx = FirmContext(firm_id=leased.firm_id, user_id="agent", role="admin")
        if not self.billing.check_entitlement(ctx, "adjudicate_claim").allowed:
            self.release_claim(leased.claim_id, undo_attempt=True)
            log_event(log, "worker_claim_not_entitled", claim_id=str(leased.claim_id), firm_id=str(leased.firm_id))
            return False
        t0 = time.monotonic()
        try:
            outcome = self.adjudicate(leased)
        except BudgetExhausted:
            self.release_claim(leased.claim_id, undo_attempt=True)
            raise
        except Exception as exc:  # noqa: BLE001 - logged; the lease expires and a later attempt retries
            log_event(
                log,
                "worker_claim_error",
                level=40,
                claim_id=str(leased.claim_id),
                error=f"{type(exc).__name__}: {exc}"[:400],
                attempt=leased.attempts,
            )
            return True
        log_event(
            log,
            "worker_claim_done",
            claim_id=str(leased.claim_id),
            outcome=outcome,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        return True

    @contextmanager
    def singleton(self) -> Iterator[None]:
        conn = self.engine.connect()
        try:
            got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar_one()
            conn.commit()
            if not got:
                raise AnotherWorkerRunning("another worker holds the lock; run one worker per database")
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock_all()"))
            conn.close()

    def run_forever(self, max_idle_ticks: int | None = None) -> int:
        idle = 0
        with self.singleton():
            log_event(
                log,
                "worker_started",
                worker_id=self.worker_id,
                adjudicator=self.settings.adjudicator,
                variant=self.config.variant,
                llm_mode=self.settings.llm_mode,
            )
            while not self.stopped:
                self.heartbeat({"adjudicator": self.settings.adjudicator, "variant": self.config.variant})
                try:
                    did = self.tick()
                except BudgetExhausted as exc:
                    log_event(log, "worker_budget_exhausted", level=40, error=str(exc))
                    return 3
                idle = 0 if did else idle + 1
                if max_idle_ticks is not None and idle >= max_idle_ticks:
                    return 0
                if not did:
                    time.sleep(self.settings.worker_poll_s)
        return 0


def charged_cost(engine: Engine, run_id: UUID) -> Decimal:
    with engine.connect() as conn:
        value = conn.execute(text("SELECT cost_usd FROM agent_runs WHERE id = :r"), {"r": run_id}).scalar_one()
    return Decimal(value or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="drain the queue, then exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure(settings.log_level)
    worker = Worker(settings=settings)

    def stop(*_: object) -> None:
        worker.stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return worker.run_forever(max_idle_ticks=1 if args.once else None)
    except AnotherWorkerRunning as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
