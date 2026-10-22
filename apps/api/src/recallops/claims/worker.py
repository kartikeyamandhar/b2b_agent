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

A claim that fails WORKER_MAX_ATTEMPTS times is held for a person (status capped) and its run is closed as failed
(not resumable; the reviewer's action applies directly). When the global model budget is spent (BudgetExhausted), or a
product has had INTAKE_AGENT_CLAIMS_PER_PROGRAM_DAY agent runs today, claims are decided by the rules checker instead,
so the worker never stops for every company (plan v3). Companies take turns, the oldest claim first within each, and
a product in draft does not run. A firm that is over its plan's limit is skipped for one polling interval, so it never
blocks other firms. Every SWEEP_EVERY_S the worker deletes drafts nobody submitted within DRAFT_TTL_HOURS. On start
and on idle ticks the worker recovers adjudication runs left running by a crash after the claim was decided or held:
the claim's status says whether the run reached its interrupt (waiting_human, capped) or finished (done).

After a claim's decision has committed, the worker projects it into the fraud graph (recallops.fraud.sync), which
rewrites fraud_clusters; idle ticks re-project claims whose facts changed. The graph never decides anything: a sync
failure is logged and the claim stays dirty for a later tick, and a graph outage only delays clusters (money safety
is in the decision's own transaction, claims/effects.py).
"""

from __future__ import annotations

import argparse
import json
import logging
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

from recallops import access
from recallops.billing.interface import BillingGate
from recallops.billing.policy import PlanBilling
from recallops.claims.agent_tools import ToolContext
from recallops.claims.context import LoadedClaim, load_claim, photo_reader, program_claims
from recallops.claims.effects import POST_DECISION_HOLDS, DbEffects
from recallops.claims.graph import VARIANTS, AgentConfig, build_graph, run_budget, summarize
from recallops.claims.rules_only import adjudicate as rules_only_adjudicate
from recallops.claims.wording import claimant_question
from recallops.db.engine import app_engine, internal_engine
from recallops.fraud.graph import GraphError, GraphStore, GraphUnavailable, build_graph_store
from recallops.fraud.graph_admin import ensure_schema
from recallops.fraud.sync import check_epoch, resync_dirty, sync_claim
from recallops.llm.client import LlmClient, build_client
from recallops.llm.meter import BudgetExhausted
from recallops.logs import configure, get_logger, log_event
from recallops.models import WorkerHeartbeat
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.ledger import PayoutLedger
from recallops.retrieval.embed import build_embedder
from recallops.retrieval.search import PgRetriever
from recallops.settings import Settings, get_settings
from recallops.storage import Storage, build_storage
from recallops.tenancy import FirmContext
from recallops.tracing.writer import DbTrace

log = get_logger("claims.worker")

LOCK_KEY = 7_420_017  # pg advisory lock id for "the one worker"
BUDGET_FALLBACK_S = 3600.0  # after the model budget is spent, the rules checker decides claims for this long
SWEEP_EVERY_S = 600.0  # how often abandoned drafts are swept
NIL_FIRM = UUID(int=0)
AGENT_UNAVAILABLE = "agent_unavailable"  # hold reason: the rules checker approved while the agent was unavailable


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
        graph: GraphStore | bool | None = True,
        storage: Storage | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or internal_engine()
        self.billing = billing or PlanBilling(self.engine, self.settings)
        self.ledger = ledger or DbPayoutLedger(self.engine)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._llm = llm
        self.config: AgentConfig = VARIANTS[self.settings.agent_variant]
        self.embedder = build_embedder(self.settings.embedding_model_id, self.settings.embedding_dim)
        self.stopped = False
        self.blocked_firms: dict[UUID, float] = {}  # firm -> monotonic expiry (not entitled to adjudicate)
        self.last_firm: UUID = NIL_FIRM  # companies take turns (lease_claim)
        self.budget_fallback_until = 0.0  # monotonic: until then claims use the rules checker
        self.next_sweep = 0.0
        # graph=True builds the store from settings (None when NEO4J_URI is empty); False or None disables it.
        self.graph: GraphStore | None = (
            build_graph_store(self.settings) if graph is True else (graph if graph is not False else None)
        )
        self.app_engine = app_engine()
        self.storage: Storage = storage or build_storage(self.settings)  # the worker's own, read by vision tools

    # fraud graph projection (never decides anything)

    def graph_start(self) -> None:
        if self.graph is None:
            return
        try:
            ensure_schema(self.graph)
            check_epoch(self.graph, self.engine, self.settings)
        except GraphError as exc:
            log_event(log, "graph_start_failed", level=30, error=str(exc)[:300])

    def graph_sync(self, claim_id: UUID) -> None:
        """After the claim's decision committed. Any failure leaves the claim dirty for resync_dirty."""
        if self.graph is None:
            return
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT firm_id, program_id FROM claims WHERE id = :c"), {"c": claim_id}).first()
        if row is None:
            return
        try:
            sync_claim(self.graph, self.engine, self.app_engine, self.settings, row.firm_id, row.program_id, claim_id)
        except Exception as exc:  # noqa: BLE001 - the projection must never fail a decided claim
            log_event(
                log, "graph_sync_failed", level=30, claim_id=str(claim_id), error=f"{type(exc).__name__}: {exc}"[:300]
            )

    def graph_resync(self) -> int:
        if self.graph is None:
            return 0
        try:
            return resync_dirty(
                self.graph,
                self.engine,
                self.app_engine,
                self.settings,
                self.settings.graph_resync_batch,
                heartbeat=lambda: self.heartbeat({"adjudicator": self.settings.adjudicator, "resync": True}),
            )
        except GraphUnavailable as exc:
            log_event(log, "graph_unavailable", level=30, error=str(exc)[:300])
            return 0

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
        """The next submitted claim, taking companies in turn (plan v3: one company's flood, through its public link,
        must not hold up everyone else's claims), the oldest first within a company. A product in draft does not run.
        """
        now = time.monotonic()
        self.blocked_firms = {f: t for f, t in self.blocked_firms.items() if t > now}
        where = (
            "c.status = 'submitted' AND (c.lease_until IS NULL OR c.lease_until < now()) "
            "AND EXISTS (SELECT 1 FROM recall_programs pd WHERE pd.id = c.program_id AND pd.status <> 'draft') "
            "AND (CAST(:access_off AS boolean) OR EXISTS (SELECT 1 FROM recall_programs pr "
            f"WHERE pr.id = c.program_id AND {access.approved_program_sql('pr')})) "
            "AND (c.firm_id <> ALL(CAST(:blocked AS uuid[])) "
            "OR EXISTS (SELECT 1 FROM claim_usage u WHERE u.firm_id = c.firm_id AND u.claim_id = c.id "
            "AND u.period_start = :period))"
        )
        params = {
            "me": self.worker_id,
            "ttl": self.settings.worker_lease_s,
            "blocked": [str(f) for f in self.blocked_firms],
            "period": datetime.now(UTC).date().replace(day=1),
            "access_off": not access.required(self.settings),
            "last": str(self.last_firm),
        }
        lease = (
            "UPDATE claims SET lease_owner = :me, lease_until = now() + make_interval(secs => :ttl), "
            "attempts = attempts + 1 WHERE id = (SELECT c.id FROM claims c WHERE {where} "
            "ORDER BY c.submitted_at NULLS LAST, c.id FOR UPDATE OF c SKIP LOCKED LIMIT 1) "
            "RETURNING id, firm_id, attempts"
        )
        with self.engine.begin() as conn:
            firm = conn.execute(
                text(
                    f"SELECT c.firm_id FROM claims c WHERE {where} GROUP BY c.firm_id "
                    "ORDER BY (c.firm_id <= CAST(:last AS uuid)), c.firm_id LIMIT 1"
                ),
                params,
            ).scalar_one_or_none()
            if firm is None:
                return None
            row = conn.execute(
                text(lease.format(where=where + " AND c.firm_id = :firm")), {**params, "firm": firm}
            ).first()
            if row is None:  # that company's claims were taken meanwhile: any company's next claim
                row = conn.execute(text(lease.format(where=where)), params).first()
        if row is None:
            return None
        self.last_firm = row.firm_id
        return Leased(row.id, row.firm_id, row.attempts)

    def sweep_due(self) -> bool:
        return time.monotonic() >= self.next_sweep

    def sweep_drafts(self) -> int:
        """Claims started from a public link and never submitted are deleted after DRAFT_TTL_HOURS, with their photos
        (plan v3: started claims would otherwise pile up in the queue and the photo bucket). A draft a campaign
        registration still points to is kept. Objects are deleted only after the rows are gone."""
        self.next_sweep = time.monotonic() + SWEEP_EVERY_S
        old = (
            "c.status = 'draft' AND c.created_at < now() - make_interval(hours => :h) "
            "AND (c.lease_until IS NULL OR c.lease_until < now()) "
            "AND NOT EXISTS (SELECT 1 FROM registrations r WHERE r.claim_id = c.id)"
        )
        try:
            with self.engine.begin() as conn:
                # Lock the drafts first: a claimant who submits at the 48-hour mark waits for this transaction, and
                # only the rows locked here lose their photos (plan v3 review: reading paths and deleting in two
                # unlocked statements removed the photos of a claim submitted in between).
                ids = (
                    conn.execute(
                        text(f"SELECT c.id FROM claims c WHERE {old} FOR UPDATE OF c SKIP LOCKED"),
                        {"h": self.settings.draft_ttl_hours},
                    )
                    .scalars()
                    .all()
                )
                paths = (
                    conn.execute(
                        text("SELECT storage_path FROM claim_photos WHERE claim_id = ANY(:ids)"), {"ids": list(ids)}
                    )
                    .scalars()
                    .all()
                    if ids
                    else []
                )
                gone = (
                    conn.execute(
                        text("DELETE FROM claims WHERE id = ANY(:ids) AND status = 'draft' RETURNING id"),
                        {"ids": list(ids)},
                    ).all()
                    if ids
                    else []
                )
        except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the worker
            log_event(log, "draft_sweep_failed", level=30, error=type(exc).__name__)
            return 0
        for path in paths:
            try:
                self.storage.delete(path)
            except Exception as exc:  # noqa: BLE001 - an object left behind is logged, the rows are already gone
                log_event(log, "draft_photo_delete_failed", level=30, error=type(exc).__name__)
        if gone:
            log_event(log, "drafts_swept", claims=len(gone), photos=len(paths))
        return len(gone)

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
                    "AND (status IN ('waiting_human', 'capped', 'failed') OR (status = 'running' "
                    "AND lease_owner IS NOT NULL)) "
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
            photo_bytes=photo_reader(self.storage, loaded.photo_paths),
            retriever=retriever,
            llm=self.llm,
            phash_bits=self.settings.phash_duplicate_bits,
        )

    def _effects(self, loaded: LoadedClaim, lease_owner: str | None, run_id: UUID | None = None) -> DbEffects:
        ctx = FirmContext(firm_id=loaded.firm_id, user_id="agent", role="admin")
        return DbEffects(
            self.engine,
            ctx,
            loaded.claim_id,
            loaded.refund_cents,
            self.ledger,
            self.billing,
            lease_owner,
            self.settings.phash_duplicate_bits,
            run_id=run_id,
        )

    def _existing_run(self, claim_id: UUID) -> UUID | None:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT id FROM agent_runs WHERE claim_id = :c AND graph = 'adjudicate' AND status = 'running' "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"c": claim_id},
            ).scalar_one_or_none()

    def _record_usage(self, firm_id: UUID, claim_id: UUID, run_id: UUID, cost: Decimal) -> None:
        self.billing.record_usage(FirmContext(firm_id=firm_id, user_id="agent", role="admin"), claim_id, run_id, cost)

    def agent_allowed(self, program_id: UUID) -> bool:
        """Platform safety, the same on every plan (plan v3): at most INTAKE_AGENT_CLAIMS_PER_PROGRAM_DAY claims a
        product a day go to the AI agent, and none while the model budget is spent; the rest use the rules checker,
        so a public link cannot spend the model budget for every company."""
        if time.monotonic() < self.budget_fallback_until:
            return False
        with self.engine.connect() as conn:
            today = conn.execute(
                text(
                    "SELECT count(*) FROM agent_runs WHERE program_id = :p AND graph = 'adjudicate' "
                    "AND started_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
                ),
                {"p": program_id},
            ).scalar_one()
        return int(today) < self.settings.intake_agent_claims_per_program_day

    def _close_for_fallback(self, run_id: UUID) -> None:
        """Closed as failed before the rules checker decides the claim; left running, recover_orphans would read the
        claim's hold as this run's interrupt and mark a run that never reached one waiting_human."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE agent_runs SET status = 'failed', finished_at = now(), lease_owner = NULL, "
                    "lease_until = NULL, outcome = coalesce(outcome, '{}'::jsonb) "
                    "|| '{\"budget_fallback\": true}'::jsonb WHERE id = :r AND status = 'running'"
                ),
                {"r": run_id},
            )

    def adjudicate(self, leased: Leased) -> str:
        with Session(self.engine) as session:
            loaded = load_claim(session, leased.claim_id)
        if self.settings.adjudicator == "rules_only":
            return self._rules_only(loaded)
        attached = self._existing_run(loaded.claim_id)
        budget_spent = time.monotonic() < self.budget_fallback_until
        if budget_spent or (attached is None and not self.agent_allowed(loaded.program_id)):
            if attached is not None:  # a run the spent budget stopped halfway can never finish
                self._close_for_fallback(attached)
            log_event(
                log, "worker_agent_quota_fallback", claim_id=str(loaded.claim_id), program_id=str(loaded.program_id)
            )
            return self._rules_only(loaded, fallback=True)
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
                self._effects(loaded, self.worker_id, trace.run_id),
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
        self._record_usage(loaded.firm_id, loaded.claim_id, trace.run_id, charged_cost(self.engine, trace.run_id))
        return result.outcome or (result.held or {}).get("reason", "held")

    def _rules_only(self, loaded: LoadedClaim, fallback: bool = False) -> str:
        """fallback: the agent was unavailable (the product's daily quota, or a spent model budget). The rules checker
        never looks at a photo (presence only), so an approval it reaches then waits for a person instead of paying
        (hold_reason agent_unavailable, plan v3 review); denials and questions still go out at once. In
        ADJUDICATOR=rules_only mode the checker decides on its own, as before."""
        with Session(self.engine) as session:
            others = program_claims(session, loaded.firm_id, loaded.program_id, loaded.claim_id)
        trace = DbTrace(self.engine, "rules_only", loaded.firm_id, loaded.claim_id, loaded.program_id)
        result = rules_only_adjudicate(
            loaded.facts, loaded.rules, others, loaded.value_threshold_cents, trace, self.settings.phash_duplicate_bits
        )
        effects = self._effects(loaded, self.worker_id, trace.run_id)
        decision = {
            "outcome": result.outcome,
            "reasons": result.reasons,
            "cited_rules": result.cited_rules,
            "question_for_claimant": claimant_question(result.reasons) if result.outcome == "needs_info" else None,
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
        elif fallback and result.outcome == "approve":
            effects.hold({"reason": AGENT_UNAVAILABLE, "decision": decision, "claim_status": "under_review"})
        else:
            effects.apply(decision)
        self.release_claim(loaded.claim_id)
        self._record_usage(loaded.firm_id, loaded.claim_id, trace.run_id, Decimal(0))
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
                self._effects(loaded, None, trace.run_id),
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
        self._record_usage(loaded.firm_id, loaded.claim_id, run_id, charged_cost(self.engine, run_id))
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
        with self.engine.begin() as conn:  # the crashed run can never be resumed: close it
            conn.execute(
                text(
                    "UPDATE agent_runs SET status = 'failed', finished_at = now(), lease_owner = NULL, "
                    "lease_until = NULL, outcome = CAST(:o AS jsonb) WHERE claim_id = :c AND graph = 'adjudicate' "
                    "AND status = 'running'"
                ),
                {"c": leased.claim_id, "o": json.dumps({"exhausted": True, "attempts": leased.attempts - 1})},
            )
        self.release_claim(leased.claim_id)

    def park_resume(self, run_id: UUID, claim_id: UUID, exc: Exception) -> None:
        """A resume that raised: count it on the run and release the lease so a later tick retries; after
        WORKER_MAX_ATTEMPTS the run is closed as failed (exhausted) with the reviewer's payload kept in its outcome,
        and the claim stays with a person, whose next action applies directly."""
        error = f"{type(exc).__name__}: {exc}"[:400]
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE agent_runs SET attempts = attempts + 1, lease_owner = NULL, lease_until = NULL, "
                    "status = CASE WHEN status = 'running' THEN 'waiting_human' ELSE status END "
                    "WHERE id = :r RETURNING attempts"
                ),
                {"r": run_id},
            ).first()
            if row is not None and row.attempts >= self.settings.worker_max_attempts:
                conn.execute(
                    text(
                        "UPDATE agent_runs SET status = 'failed', finished_at = now(), outcome = "
                        "coalesce(outcome, '{}'::jsonb) || jsonb_build_object('exhausted', true, 'resume_error', "
                        "CAST(:e AS text), 'unapplied_review', resume_payload), resume_payload = NULL WHERE id = :r"
                    ),
                    {"r": run_id, "e": error},
                )
        log_event(log, "worker_resume_error", level=40, run_id=str(run_id), claim_id=str(claim_id), error=error)

    def recover_orphans(self) -> int:
        """Adjudication runs still running whose claim is no longer submitted and not leased: a crash between the
        claim's decision (committed by route) and the worker's status write. The claim tells which: a held claim
        means the interrupt was reached, a decided claim means the run finished."""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    "UPDATE agent_runs r SET status = CASE WHEN c.decision->>'hold_reason' = ANY(:post) THEN 'done' "
                    "WHEN c.status = 'capped' THEN 'capped' "
                    "WHEN c.status IN ('flagged', 'under_review') THEN 'waiting_human' ELSE 'done' END, "
                    "finished_at = CASE WHEN c.status IN ('flagged', 'under_review', 'capped') "
                    "AND coalesce(c.decision->>'hold_reason', '') <> ALL(:post) THEN NULL ELSE now() END, "
                    "outcome = coalesce(r.outcome, '{}'::jsonb) || '{\"recovered\": true}'::jsonb "
                    "FROM claims c WHERE c.id = r.claim_id AND r.graph = 'adjudicate' AND r.status = 'running' "
                    "AND r.resume_payload IS NULL AND c.status NOT IN ('submitted', 'draft') "
                    "AND (c.lease_until IS NULL OR c.lease_until < now()) "
                    "AND (r.lease_until IS NULL OR r.lease_until < now()) RETURNING r.id, r.status"
                ),
                {"post": list(POST_DECISION_HOLDS)},
            ).all()
        for row in rows:
            log_event(log, "worker_run_recovered", level=30, run_id=str(row.id), status=row.status)
        return len(rows)

    def recover_usage_costs(self) -> int:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT r.firm_id, r.claim_id, r.id, r.cost_usd FROM agent_runs r "
                    "JOIN run_usage_costs u ON u.run_id = r.id AND u.firm_id = r.firm_id "
                    "WHERE r.cost_usd > u.recorded_cost"
                )
            ).all()
        recovered = 0
        for firm_id, claim_id, run_id, cost in rows:
            try:
                self._record_usage(firm_id, claim_id, run_id, Decimal(cost))
                recovered += 1
            except Exception as exc:  # noqa: BLE001 - one run's usage row must not stop the worker
                log_event(
                    log,
                    "usage_recovery_failed",
                    level=logging.ERROR,
                    firm_id=str(firm_id),
                    run_id=str(run_id),
                    error=type(exc).__name__,
                )
        return recovered

    def tick(self) -> bool:
        """One unit of work. True when something was done."""
        resume = self.lease_resume()
        if resume is not None:
            try:
                self.resume(*resume)
            except Exception as exc:  # noqa: BLE001 - contained: a failing resume (a spent budget too) must not stop the worker
                self.park_resume(resume[0], resume[1], exc)
                return True
            self.graph_sync(resume[1])
            return True
        leased = self.lease_claim()
        if leased is None:
            return False
        if leased.attempts > self.settings.worker_max_attempts:
            self.hold_exhausted(leased)
            return True
        ctx = FirmContext(firm_id=leased.firm_id, user_id="agent", role="admin")
        if not self.billing.check_entitlement(ctx, "adjudicate_claim", leased.claim_id).allowed:
            self.release_claim(leased.claim_id, undo_attempt=True)
            self.blocked_firms[leased.firm_id] = time.monotonic() + self.settings.worker_poll_s
            log_event(log, "worker_claim_not_entitled", claim_id=str(leased.claim_id), firm_id=str(leased.firm_id))
            return True  # the next lease skips this firm, so other firms' claims are not blocked
        t0 = time.monotonic()
        try:
            outcome = self.adjudicate(leased)
        except BudgetExhausted as exc:
            # The model budget is spent: claims go to the rules checker for a while instead of every company's claims
            # waiting on a stopped worker (plan v3). The run it stopped halfway is closed when the claim is leased
            # again (adjudicate).
            self.release_claim(leased.claim_id, undo_attempt=True)
            self.budget_fallback_until = time.monotonic() + BUDGET_FALLBACK_S
            log_event(log, "worker_budget_exhausted", level=40, error=str(exc), fallback_s=BUDGET_FALLBACK_S)
            return True
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
        self.graph_sync(leased.claim_id)
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
            self.recover_orphans()
            self.recover_usage_costs()
            self.graph_start()
            while not self.stopped:
                self.heartbeat({"adjudicator": self.settings.adjudicator, "variant": self.config.variant})
                did = self.tick()
                idle = 0 if did else idle + 1
                if self.sweep_due():
                    self.sweep_drafts()
                if not did and (self.recover_orphans() or self.recover_usage_costs() or self.graph_resync()):
                    idle = 0
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
