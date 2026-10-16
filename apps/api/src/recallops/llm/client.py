"""The one model client. Every model call in the codebase goes through LlmClient.complete, which runs the
meter's preflight before the transport and settles actual cost after it. The anthropic SDK is imported
only in this package (ruff banned-api elsewhere).

Modes (LLM_MODE): fake (tests inject a transport), replay (recorded responses, a miss is an error, never
a live call), live (the SDK; needs the owner's approval and a real key in .env).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from recallops.llm.meter import CostMeter, DbSpendLedger, RunBudget
from recallops.llm.pricing import PriceTable, Usage, estimate_image_tokens, estimate_text_tokens
from recallops.logs import get_logger, log_event
from recallops.settings import Settings

log = get_logger("llm.client")


class LiveCallsDisabled(RuntimeError):
    pass


class ReplayMiss(LookupError):
    pass


class ModelCallFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageInput:
    media_type: str
    data_b64: str
    width: int
    height: int


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user_text: str
    output_schema: dict[str, Any] | None = None
    images: tuple[ImageInput, ...] = ()
    max_tokens: int = 2048
    purpose: str = "generic"

    def canonical_key(self, model_id: str) -> str:
        body = {
            "model": model_id,
            "system": self.system,
            "user_text": self.user_text,
            "schema": self.output_schema,
            "images": [hashlib.sha256(i.data_b64.encode()).hexdigest() for i in self.images],
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: Usage
    stop_reason: str | None
    model_id: str
    request_id: str | None = None


@dataclass(frozen=True)
class Completion:
    response: ModelResponse
    cost_usd: Any  # Decimal
    latency_ms: int
    max_tokens: int


class Transport(Protocol):
    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse: ...


class FakeTransport:
    """Scripted responses for tests and the M1 fake-model graph tests. Counts every call."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[ModelRequest, int]] = []

    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse:
        self.calls.append((request, max_tokens))
        if not self._responses:
            raise ModelCallFailed("fake transport has no scripted response left")
        return self._responses.pop(0)


@dataclass
class ReplayTransport:
    """Recorded responses keyed by the canonical request. Writes nothing; a miss raises ReplayMiss."""

    directory: Path
    calls: list[str] = field(default_factory=list)

    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse:
        key = request.canonical_key(model_id)
        self.calls.append(key)
        path = self.directory / f"{key}.json"
        if not path.exists():
            raise ReplayMiss(f"no recording for {request.purpose} ({key[:12]}) in {self.directory}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ModelResponse(
            text=data["text"],
            usage=Usage(**data["usage"]),
            stop_reason=data.get("stop_reason"),
            model_id=data["model_id"],
            request_id=data.get("request_id"),
        )


class AnthropicTransport:
    """Live calls through the pinned anthropic SDK. SDK retries are off so the meter sees every attempt."""

    def __init__(self, api_key: str) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse:
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data_b64}}
            for i in request.images
        ]
        content.append({"type": "text", "text": request.user_text})
        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
        }
        if request.output_schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": request.output_schema}}
        try:
            message = self._client.with_options(timeout=timeout_s).messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise ModelCallFailed(f"{type(exc).__name__}: {exc}") from exc
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return ModelResponse(
            text=text,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
            stop_reason=message.stop_reason,
            model_id=message.model,
            request_id=getattr(message, "_request_id", None),
        )


class LlmClient:
    def __init__(self, transport: Transport, meter: CostMeter, model_id: str, settings: Settings) -> None:
        self.transport = transport
        self.meter = meter
        self.model_id = model_id
        self.settings = settings

    def estimate_input_tokens(self, request: ModelRequest) -> int:
        cpt = self.settings.llm_chars_per_token_estimate
        text = estimate_text_tokens(request.system, cpt) + estimate_text_tokens(request.user_text, cpt)
        if request.output_schema is not None:
            text += estimate_text_tokens(json.dumps(request.output_schema), cpt)
        images = sum(estimate_image_tokens(i.width, i.height, self.settings.llm_image_patch_px) for i in request.images)
        return text + images

    def complete(self, request: ModelRequest, run: RunBudget) -> Completion:
        input_est = self.estimate_input_tokens(request)
        pre = self.meter.preflight(self.model_id, input_est, request.max_tokens, run)
        t0 = time.monotonic()
        response = self.transport.send(request, self.model_id, pre.max_tokens, pre.timeout_s)
        latency_ms = round((time.monotonic() - t0) * 1000)
        cost = self.meter.settle(self.model_id, response.usage, run)
        log_event(
            log,
            "llm_call_settled",
            purpose=request.purpose,
            model_id=self.model_id,
            tokens_in=response.usage.total_input,
            tokens_out=response.usage.output_tokens,
            cost_usd=str(cost),
            duration_ms=latency_ms,
        )
        return Completion(response=response, cost_usd=cost, latency_ms=latency_ms, max_tokens=pre.max_tokens)


def build_client(settings: Settings, transport: Transport | None = None) -> LlmClient:
    """Production wiring: replay by default; live only with LLM_MODE=live and a real key."""
    from recallops.db.engine import internal_engine

    if transport is None:
        if settings.llm_mode == "replay":
            transport = ReplayTransport(settings.replay_dir)
        elif settings.llm_mode == "live":
            if settings.anthropic_api_key in {"", "sk-ant-fake"}:
                raise LiveCallsDisabled("LLM_MODE=live needs a real ANTHROPIC_API_KEY in .env")
            transport = AnthropicTransport(settings.anthropic_api_key)
        else:
            raise LiveCallsDisabled("LLM_MODE=fake needs an injected transport")
    meter = CostMeter(
        prices=PriceTable.from_settings(settings),
        ledger=DbSpendLedger(internal_engine(), settings.llm_spend_carried_usd),
        budget_stop_usd=settings.llm_budget_stop_usd,
        budget_warn_usd=settings.llm_budget_warn_usd,
        call_timeout_s=settings.llm_timeout_s,
    )
    log_event(log, "llm_client_built", mode=settings.llm_mode, model_id=settings.llm_model_id)
    return LlmClient(transport, meter, settings.llm_model_id, settings)
