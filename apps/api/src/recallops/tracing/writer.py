"""agent_runs and agent_steps writer: the only trace path (CLAUDE.md invariant 3). The UI trace viewer
reads these rows; the row shape is documented in docs/contracts/trace.md.

Step bookkeeping (migration 0002): `counted` marks rows that count toward the run's step cap (model calls and
rejected tool calls), `nominal_cost_usd` is the cost from reported usage (also for replayed responses, whose
charged `cost_usd` is 0), and `step_key` makes a node's rows idempotent when LangGraph re-executes the node after a
crash or a resume: a second write with the same key is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.logs import get_logger, log_event
from recallops.models import AgentRun, AgentStep

log = get_logger("tracing")

Graph = Literal["extract_rules", "adjudicate", "rules_only", "rules_plus_vision"]
RunStatus = Literal["running", "done", "capped", "failed", "waiting_human"]


@dataclass(frozen=True)
class StepRecord:
    idx: int
    node: str
    thought: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal(0)
    counted: bool = False
    nominal_cost_usd: Decimal = Decimal(0)
    step_key: str | None = None


@dataclass(frozen=True)
class BudgetSnapshot:
    """What a run has already used, rebuilt from its rows when a process attaches to it."""

    counted_steps: int = 0
    nominal_cost_usd: Decimal = Decimal(0)
    seconds: float = 0.0


class TraceSink(Protocol):
    run_id: UUID

    def step(
        self,
        node: str,
        *,
        thought: str | None = None,
        tool: str | None = None,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: Decimal = Decimal(0),
        counted: bool = False,
        nominal_cost_usd: Decimal | None = None,
        step_key: str | None = None,
    ) -> StepRecord: ...

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None: ...

    def snapshot(self) -> BudgetSnapshot: ...


@dataclass
class MemoryTrace:
    """Same contract, kept in memory (unit tests and offline evals that do not need the database)."""

    graph: Graph
    run_id: UUID = field(default_factory=uuid4)
    steps: list[StepRecord] = field(default_factory=list)
    status: RunStatus = "running"
    outcome: dict[str, Any] | None = None

    def step(self, node: str, **kwargs: Any) -> StepRecord:
        key = kwargs.get("step_key")
        if key is not None:
            existing = next((s for s in self.steps if s.step_key == key), None)
            if existing is not None:
                return existing
        if kwargs.get("nominal_cost_usd") is None:
            kwargs["nominal_cost_usd"] = kwargs.get("cost_usd", Decimal(0))
        record = StepRecord(idx=len(self.steps), node=node, **kwargs)
        self.steps.append(record)
        return record

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None:
        self.status, self.outcome = status, outcome

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            counted_steps=sum(1 for s in self.steps if s.counted),
            nominal_cost_usd=sum((s.nominal_cost_usd for s in self.steps), Decimal(0)),
            seconds=sum(s.latency_ms for s in self.steps) / 1000,
        )

    @property
    def cost_usd(self) -> Decimal:
        return sum((s.cost_usd for s in self.steps), Decimal(0))

    @property
    def nominal_cost_usd(self) -> Decimal:
        return sum((s.nominal_cost_usd for s in self.steps), Decimal(0))


class DbTrace:
    """Writes one agent_runs row and one agent_steps row per node, each in its own short transaction so a
    crash leaves every completed step visible. attach() continues an existing run (resume or crash recovery)."""

    def __init__(
        self,
        engine: Engine,
        graph: Graph,
        firm_id: UUID | None,
        claim_id: UUID | None = None,
        program_id: UUID | None = None,
        recall_id: UUID | None = None,
        model_id: str | None = None,
        config: dict[str, Any] | None = None,
        *,
        tenant: bool = False,
        _run_id: UUID | None = None,
    ) -> None:
        """tenant=True: the engine is the API's recallops_app role, so every transaction sets app.firm_id first
        (RLS); jobs pass the internal engine and leave it False."""
        self.engine = engine
        self.firm_id = firm_id
        self.graph = graph
        self.tenant = tenant
        if _run_id is not None:
            self.run_id = _run_id
            with Session(engine) as session:
                last = session.scalar(select(func.max(AgentStep.idx)).where(AgentStep.run_id == _run_id))
                charged = session.scalar(
                    select(func.coalesce(func.sum(AgentStep.cost_usd), 0)).where(AgentStep.run_id == _run_id)
                )
            self._idx = -1 if last is None else int(last)
            self._idx += 1
            self._cost = Decimal(charged or 0)
            log_event(log, "agent_run_attached", run_id=str(self.run_id), graph=graph, next_idx=self._idx)
            return
        self._idx = 0
        self._cost = Decimal(0)
        with self._session() as session:
            run = AgentRun(
                firm_id=firm_id,
                claim_id=claim_id,
                program_id=program_id,
                recall_id=recall_id,
                graph=graph,
                model_id=model_id,
                config=config or {},
            )
            session.add(run)
            session.flush()
            self.run_id = run.id
            run.thread_id = str(run.id)  # one LangGraph thread per run, never per claim
        log_event(
            log,
            "agent_run_started",
            run_id=str(self.run_id),
            graph=graph,
            firm_id=str(firm_id) if firm_id else None,
            claim_id=str(claim_id) if claim_id else None,
        )

    @classmethod
    def attach(cls, engine: Engine, run_id: UUID) -> DbTrace:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise LookupError(f"no agent run {run_id}")
            firm_id, graph = run.firm_id, run.graph
        return cls(engine, graph, firm_id, _run_id=run_id)  # type: ignore[arg-type]

    def step(self, node: str, **kwargs: Any) -> StepRecord:
        if kwargs.get("nominal_cost_usd") is None:
            kwargs["nominal_cost_usd"] = kwargs.get("cost_usd", Decimal(0))
        record = StepRecord(idx=self._idx, node=node, **kwargs)
        with self._session() as session:
            values = {
                "run_id": self.run_id,
                "firm_id": self.firm_id,
                "idx": record.idx,
                "node": node,
                "thought": record.thought,
                "tool": record.tool,
                "args": record.args,
                "result": record.result,
                "latency_ms": record.latency_ms,
                "tokens_in": record.tokens_in,
                "tokens_out": record.tokens_out,
                "cost_usd": record.cost_usd,
                "counted": record.counted,
                "nominal_cost_usd": record.nominal_cost_usd,
                "step_key": record.step_key,
            }
            stmt = insert(AgentStep).values(**values)
            if record.step_key is not None:
                stmt = stmt.on_conflict_do_nothing(constraint="agent_steps_run_step_key")
            inserted = session.execute(stmt.returning(AgentStep.idx)).first()
            if inserted is None:
                log_event(log, "agent_step_replayed", run_id=str(self.run_id), node=node, step_key=record.step_key)
                return record
            self._idx += 1
            self._cost += record.cost_usd
            session.execute(
                update(AgentRun).where(AgentRun.id == self.run_id).values(steps=self._idx, cost_usd=self._cost)
            )
        log_event(
            log,
            "agent_step",
            run_id=str(self.run_id),
            node=node,
            tool=record.tool,
            counted=record.counted,
            duration_ms=record.latency_ms,
            cost_usd=str(record.cost_usd),
        )
        return record

    def _session(self) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def scope() -> Any:
            with Session(self.engine) as session, session.begin():
                if self.tenant and self.firm_id is not None:
                    from recallops.db.session import set_firm

                    set_firm(session, self.firm_id)
                yield session

        return scope()

    def snapshot(self) -> BudgetSnapshot:
        with self._session() as session:
            row = session.execute(
                select(
                    func.count().filter(AgentStep.counted.is_(True)),
                    func.coalesce(func.sum(AgentStep.nominal_cost_usd), 0),
                    func.coalesce(func.sum(AgentStep.latency_ms), 0),
                ).where(AgentStep.run_id == self.run_id)
            ).one()
        return BudgetSnapshot(counted_steps=int(row[0]), nominal_cost_usd=Decimal(row[1]), seconds=int(row[2]) / 1000)

    def set_status(self, status: RunStatus, **values: Any) -> None:
        with self._session() as session:
            session.execute(update(AgentRun).where(AgentRun.id == self.run_id).values(status=status, **values))

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None:
        with self._session() as session:
            session.execute(
                update(AgentRun)
                .where(AgentRun.id == self.run_id)
                .values(status=status, outcome=outcome, finished_at=datetime.now(UTC))
            )
        log_event(log, "agent_run_finished", run_id=str(self.run_id), status=status)
