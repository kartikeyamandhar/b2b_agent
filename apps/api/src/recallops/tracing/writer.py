"""agent_runs and agent_steps writer: the only trace path (CLAUDE.md invariant 3). The UI trace viewer
reads these rows; the row shape is documented in docs/contracts/trace.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from recallops.logs import get_logger, log_event
from recallops.models import AgentRun, AgentStep

log = get_logger("tracing")

Graph = Literal["extract_rules", "adjudicate", "rules_only"]
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
    ) -> StepRecord: ...

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None: ...


@dataclass
class MemoryTrace:
    """Same contract, kept in memory (unit tests and offline evals that do not need the database)."""

    graph: Graph
    run_id: UUID = field(default_factory=uuid4)
    steps: list[StepRecord] = field(default_factory=list)
    status: RunStatus = "running"
    outcome: dict[str, Any] | None = None

    def step(self, node: str, **kwargs: Any) -> StepRecord:
        record = StepRecord(idx=len(self.steps), node=node, **kwargs)
        self.steps.append(record)
        return record

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None:
        self.status, self.outcome = status, outcome

    @property
    def cost_usd(self) -> Decimal:
        return sum((s.cost_usd for s in self.steps), Decimal(0))


class DbTrace:
    """Writes one agent_runs row and one agent_steps row per node, each in its own short transaction so a
    crash leaves every completed step visible."""

    def __init__(
        self,
        engine: Engine,
        graph: Graph,
        firm_id: UUID | None,
        claim_id: UUID | None = None,
        program_id: UUID | None = None,
        recall_id: UUID | None = None,
        model_id: str | None = None,
    ) -> None:
        self.engine = engine
        self.firm_id = firm_id
        self._idx = 0
        self._cost = Decimal(0)
        with Session(engine) as session, session.begin():
            run = AgentRun(
                firm_id=firm_id,
                claim_id=claim_id,
                program_id=program_id,
                recall_id=recall_id,
                graph=graph,
                model_id=model_id,
            )
            session.add(run)
            session.flush()
            self.run_id: UUID = run.id
            run.thread_id = str(run.id)
        log_event(
            log,
            "agent_run_started",
            run_id=str(self.run_id),
            graph=graph,
            firm_id=str(firm_id) if firm_id else None,
            claim_id=str(claim_id) if claim_id else None,
        )

    def step(self, node: str, **kwargs: Any) -> StepRecord:
        record = StepRecord(idx=self._idx, node=node, **kwargs)
        with Session(self.engine) as session, session.begin():
            session.add(
                AgentStep(
                    run_id=self.run_id,
                    firm_id=self.firm_id,
                    idx=record.idx,
                    node=node,
                    thought=record.thought,
                    tool=record.tool,
                    args=record.args,
                    result=record.result,
                    latency_ms=record.latency_ms,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    cost_usd=record.cost_usd,
                )
            )
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
            duration_ms=record.latency_ms,
            cost_usd=str(record.cost_usd),
        )
        return record

    def finish(self, status: RunStatus, outcome: dict[str, Any] | None = None) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(
                update(AgentRun)
                .where(AgentRun.id == self.run_id)
                .values(status=status, outcome=outcome, finished_at=datetime.now(UTC))
            )
        log_event(log, "agent_run_finished", run_id=str(self.run_id), status=status)
