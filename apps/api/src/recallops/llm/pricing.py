"""Prices for the one configured model (settings). Any other model id fails closed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from recallops.settings import Settings

MTOK = Decimal(1_000_000)


class UnpricedModel(RuntimeError):
    """The requested model has no price in settings, so its cost cannot be bounded."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens


@dataclass(frozen=True)
class PriceTable:
    model_id: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    long_threshold_tokens: int
    input_long_per_mtok: Decimal
    output_long_per_mtok: Decimal

    @classmethod
    def from_settings(cls, settings: Settings) -> PriceTable:
        return cls(
            model_id=settings.llm_model_id,
            input_per_mtok=settings.llm_price_input_per_mtok_usd,
            output_per_mtok=settings.llm_price_output_per_mtok_usd,
            long_threshold_tokens=settings.llm_price_long_context_threshold_tokens,
            input_long_per_mtok=settings.llm_price_input_long_per_mtok_usd,
            output_long_per_mtok=settings.llm_price_output_long_per_mtok_usd,
        )

    def rates(self, model_id: str, prompt_tokens: int) -> tuple[Decimal, Decimal]:
        if model_id != self.model_id:
            raise UnpricedModel(f"no price for {model_id!r}; only {self.model_id!r} is priced")
        if self.input_per_mtok <= 0 or self.output_per_mtok <= 0:
            raise UnpricedModel("prices must be positive; a zero price would disable the meter")
        if prompt_tokens > self.long_threshold_tokens:
            return self.input_long_per_mtok, self.output_long_per_mtok
        return self.input_per_mtok, self.output_per_mtok

    def cost(self, model_id: str, input_tokens: int, output_tokens: int) -> Decimal:
        in_rate, out_rate = self.rates(model_id, input_tokens)
        return (Decimal(input_tokens) * in_rate + Decimal(output_tokens) * out_rate) / MTOK

    def actual(self, model_id: str, usage: Usage) -> Decimal:
        """Cache reads and writes are billed here at the full input rate: an upper bound, never an undercount."""
        return self.cost(model_id, usage.total_input, usage.output_tokens)

    def max_output_for(self, model_id: str, input_tokens: int, budget: Decimal) -> int:
        """Largest max_tokens whose worst case fits in budget after paying for the input."""
        in_rate, out_rate = self.rates(model_id, input_tokens)
        remaining = budget - Decimal(input_tokens) * in_rate / MTOK
        if remaining <= 0:
            return 0
        return math.floor(remaining * MTOK / out_rate)


def estimate_text_tokens(text: str, chars_per_token: float) -> int:
    """Conservative: with chars_per_token 2.0 this overestimates typical English by about 2x."""
    return math.ceil(len(text) / chars_per_token) + 8


def estimate_image_tokens(width: int, height: int, patch_px: int) -> int:
    return math.ceil(width / patch_px) * math.ceil(height / patch_px)
