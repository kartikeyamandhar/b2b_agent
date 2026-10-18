"""Cost meter. The check happens before the transport is called (CLAUDE.md: projected cost before the call).

Order inside LlmClient.complete: preflight (caps, projected cost, global budget) -> transport.send ->
settle (actual cost from reported usage). A refused call raises before any request leaves the process.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from sqlalchemy import Engine, func, select

from recallops.llm.pricing import PriceTable, Usage
from recallops.logs import get_logger, log_event
from recallops.models import AgentStep

log = get_logger("llm.meter")

CapKind = Literal["steps", "usd", "seconds"]


class BudgetExhausted(RuntimeError):
    """Global model budget (LLM_BUDGET_STOP_USD) would be crossed. Stop and report."""


class RunCapExceeded(RuntimeError):
    def __init__(self, kind: CapKind, detail: str) -> None:
        super().__init__(f"{kind} cap: {detail}")
        self.kind = kind


class SpendLedger(Protocol):
    def spent_usd(self) -> Decimal: ...


class InMemoryLedger:
    def __init__(self, spent: Decimal = Decimal(0)) -> None:
        self.spent = spent

    def spent_usd(self) -> Decimal:
        return self.spent

    def add(self, amount: Decimal) -> None:
        self.spent += amount


class DbSpendLedger:
    """Total spend = carried spend from settings + sum(agent_steps.cost_usd) across all firms.

    Reads through the internal role (BYPASSRLS) because the budget is global. agent_steps is the only
    record of model cost (CLAUDE.md invariant 3: no second logging path).
    """

    def __init__(self, engine: Engine, carried: Decimal) -> None:
        self.engine = engine
        self.carried = carried

    def spent_usd(self) -> Decimal:
        with self.engine.connect() as conn:
            total = conn.execute(select(func.coalesce(func.sum(AgentStep.cost_usd), 0))).scalar_one()
        return self.carried + Decimal(total)


@dataclass
class RunBudget:
    """Per-run caps (invariant 4 for adjudication; extraction uses its own cap from settings)."""

    max_steps: int
    max_usd: Decimal
    max_seconds: float
    steps: int = 0
    cost_usd: Decimal = Decimal(0)
    active_seconds: float = 0.0  # carried over from earlier segments of the same run (resume after a pause)
    virtual_seconds: float = 0.0  # replayed calls' recorded live latency minus their replay time
    _started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return self.active_seconds + self.virtual_seconds + (time.monotonic() - self._started)

    def remaining_usd(self) -> Decimal:
        return self.max_usd - self.cost_usd


@dataclass(frozen=True)
class Preflight:
    max_tokens: int
    projected_usd: Decimal
    timeout_s: float


class CostMeter:
    def __init__(
        self,
        prices: PriceTable,
        ledger: SpendLedger,
        budget_stop_usd: Decimal,
        budget_warn_usd: Decimal,
        min_output_tokens: int = 256,
        call_timeout_s: float = 30.0,
    ) -> None:
        self.prices = prices
        self.ledger = ledger
        self.budget_stop = budget_stop_usd
        self.budget_warn = budget_warn_usd
        self.min_output_tokens = min_output_tokens
        self.call_timeout_s = call_timeout_s
        self._warned = False

    def preflight(self, model_id: str, input_tokens: int, wanted_max_tokens: int, run: RunBudget) -> Preflight:
        if run.steps + 1 > run.max_steps:
            raise RunCapExceeded("steps", f"{run.steps} of {run.max_steps} used")
        elapsed = run.elapsed()
        if elapsed >= run.max_seconds:
            raise RunCapExceeded("seconds", f"{elapsed:.1f}s of {run.max_seconds}s used")
        spent = self.ledger.spent_usd()
        global_left = self.budget_stop - spent
        if global_left <= 0:
            raise BudgetExhausted(f"spent {spent} of {self.budget_stop} USD")
        budget = min(run.remaining_usd(), global_left)
        affordable = self.prices.max_output_for(model_id, input_tokens, budget)
        max_tokens = min(wanted_max_tokens, affordable)
        if max_tokens < self.min_output_tokens:
            if run.remaining_usd() <= global_left:
                raise RunCapExceeded("usd", f"{run.cost_usd} of {run.max_usd} USD used; cannot afford a call")
            raise BudgetExhausted(f"spent {spent} of {self.budget_stop} USD; cannot afford a call")
        projected = self.prices.cost(model_id, input_tokens, max_tokens)
        timeout = max(1.0, min(self.call_timeout_s, run.max_seconds - elapsed))
        log_event(
            log,
            "llm_preflight_ok",
            model_id=model_id,
            input_tokens_est=input_tokens,
            max_tokens=max_tokens,
            projected_usd=str(projected),
            spent_usd=str(spent),
        )
        return Preflight(max_tokens=max_tokens, projected_usd=projected, timeout_s=timeout)

    def settle(self, model_id: str, usage: Usage, run: RunBudget, charged: bool = True) -> Decimal:
        """Nominal cost from usage counts against the run caps whether or not it was charged (replay parity);
        only charged cost reaches the spend ledger."""
        cost = self.prices.actual(model_id, usage)
        run.cost_usd += cost
        run.steps += 1
        if charged and isinstance(self.ledger, InMemoryLedger):
            self.ledger.add(cost)
        total = self.ledger.spent_usd()
        if not self._warned and total >= self.budget_warn:
            self._warned = True
            log_event(
                log,
                "llm_budget_warning",
                level=logging.WARNING,
                spent_usd=str(total),
                stop_usd=str(self.budget_stop),
            )
        return cost
