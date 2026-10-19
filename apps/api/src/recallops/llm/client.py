"""The one model client. Every model call in the codebase goes through LlmClient.complete, which runs the
meter's preflight before the transport and settles actual cost after it. The anthropic SDK is imported
only in this package (ruff banned-api elsewhere).

Modes (LLM_MODE): fake (tests inject a transport), replay (recorded responses, a miss is an error, never
a live call), live (the SDK; needs the owner's approval and a real key in .env). Live calls are replay-first: a
request that already has a recording is answered from it and charged nothing, so re-running an eval or resuming a
crashed claim never pays twice. Run caps always use the nominal cost from reported usage, so a replayed run makes
exactly the decisions the live run made.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
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
    """An image block. source_sha256 is the hash of the stored photo bytes; when set, it (not the re-encoded
    payload) keys the recording, so replay does not depend on how a platform's JPEG encoder resizes."""

    media_type: str
    data_b64: str
    width: int
    height: int
    source_sha256: str | None = None

    def key(self) -> str:
        if self.source_sha256:
            return f"{self.source_sha256}:{self.width}x{self.height}"
        return hashlib.sha256(self.data_b64.encode()).hexdigest()


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user_text: str
    output_schema: dict[str, Any] | None = None
    images: tuple[ImageInput, ...] = ()
    max_tokens: int = 2048
    purpose: str = "generic"
    # Multi-turn tool use: later turns (assistant content blocks and user tool_result blocks), JSON-serializable,
    # appended after the first user message; and tool definitions {name, description, input_schema}.
    messages: tuple[dict[str, Any], ...] = ()
    tools: tuple[dict[str, Any], ...] = ()

    def canonical_key(self, model_id: str) -> str:
        """Single-turn requests without tools keep the M1 key shape, so the M1 recordings still replay."""
        body: dict[str, Any] = {
            "model": model_id,
            "system": self.system,
            "user_text": self.user_text,
            "schema": self.output_schema,
            "images": [i.key() for i in self.images],
        }
        if self.messages:
            body["messages"] = list(self.messages)
        if self.tools:
            body["tools"] = list(self.tools)
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: Usage
    stop_reason: str | None
    model_id: str
    request_id: str | None = None
    content: tuple[dict[str, Any], ...] = ()  # raw content blocks (text, tool_use) for the next turn's history
    replayed: bool = False
    recorded_latency_ms: int | None = None  # live latency stored with the recording (replay's virtual clock)

    @property
    def blocks(self) -> tuple[dict[str, Any], ...]:
        return self.content or ({"type": "text", "text": self.text},)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=str(b["id"]), name=str(b["name"]), input=dict(b.get("input") or {}))
            for b in self.blocks
            if b.get("type") == "tool_use"
        ]


@dataclass(frozen=True)
class Completion:
    response: ModelResponse
    cost_usd: Any  # Decimal charged to the spend ledger: 0 when the response was replayed
    latency_ms: int
    max_tokens: int
    nominal_cost_usd: Any = None  # Decimal from reported usage, live or replayed (caps and eval cost columns)


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
        return load_recording(path)


def load_recording(path: Path) -> ModelResponse:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ModelResponse(
        text=data["text"],
        usage=Usage(**data["usage"]),
        stop_reason=data.get("stop_reason"),
        model_id=data["model_id"],
        request_id=data.get("request_id"),
        content=tuple(data.get("content") or ()),
        replayed=True,
        recorded_latency_ms=data.get("latency_ms"),
    )


UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
    }
)


def api_schema(schema: Any) -> Any:
    """Structured outputs reject numeric and length constraints; drop them for the wire. Responses are still
    validated against the full pydantic schema locally."""
    if isinstance(schema, dict):
        return {k: api_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [api_schema(v) for v in schema]
    return schema


def request_kwargs(request: ModelRequest, model_id: str, max_tokens: int) -> dict[str, Any]:
    """The Messages API body. Thinking is disabled: extraction is a copy-out task, and thinking tokens would
    compete with the JSON for max_tokens."""
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data_b64}}
        for i in request.images
    ]
    content.append({"type": "text", "text": request.user_text})
    kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens,
        "system": request.system,
        "messages": [{"role": "user", "content": content}, *request.messages],
        "thinking": {"type": "disabled"},
    }
    if request.output_schema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": api_schema(request.output_schema)}}
    if request.tools:
        # strict tools: the arguments always match the schema (tool_choice stays auto; forcing a call is refused
        # by some current models, so the system prompt steers instead).
        kwargs["tools"] = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": api_schema(t["input_schema"]),
                "strict": True,
            }
            for t in request.tools
        ]
    return kwargs


class AnthropicTransport:
    """Live calls through the pinned anthropic SDK. SDK retries are off so the meter sees every attempt."""

    def __init__(self, api_key: str) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse:
        try:
            message = self._client.with_options(timeout=timeout_s).messages.create(
                **request_kwargs(request, model_id, max_tokens)
            )
        except self._anthropic.APIError as exc:
            raise ModelCallFailed(f"{type(exc).__name__}: {exc}") from exc
        text = "".join(block.text for block in message.content if block.type == "text")
        blocks: list[dict[str, Any]] = []
        for block in message.content:
            if block.type == "text":
                blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
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
            content=tuple(blocks),
        )


@dataclass
class RecordingTransport:
    """Replay-first live transport: a request with a recording is answered from it (replayed, charged nothing);
    otherwise the live transport is called and the response saved under the canonical request key, so the same
    request replays later at no cost (ReplayTransport reads the same files)."""

    inner: Transport
    directory: Path
    recorded: list[str] = field(default_factory=list)
    replayed: list[str] = field(default_factory=list)

    def send(self, request: ModelRequest, model_id: str, max_tokens: int, timeout_s: float) -> ModelResponse:
        key = request.canonical_key(model_id)
        path = self.directory / f"{key}.json"
        if path.exists():
            self.replayed.append(key)
            return load_recording(path)
        t0 = time.monotonic()
        response = self.inner.send(request, model_id, max_tokens, timeout_s)
        latency_ms = round((time.monotonic() - t0) * 1000)
        self.directory.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "text": response.text,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": response.usage.cache_read_input_tokens,
                "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
            },
            "stop_reason": response.stop_reason,
            "model_id": response.model_id,
            "request_id": response.request_id,
            "purpose": request.purpose,
            "max_tokens": max_tokens,
            "latency_ms": latency_ms,
        }
        if response.content and any(b.get("type") != "text" for b in response.content):
            record["content"] = list(response.content)
        # Atomic: a crash mid-write must not leave a truncated recording that replay-first would load forever.
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        self.recorded.append(key)
        return response


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
        if request.messages:
            text += estimate_text_tokens(json.dumps(list(request.messages)), cpt)
        if request.tools:
            text += estimate_text_tokens(json.dumps(list(request.tools)), cpt)
        images = sum(estimate_image_tokens(i.width, i.height, self.settings.llm_image_patch_px) for i in request.images)
        return text + images

    def complete(self, request: ModelRequest, run: RunBudget) -> Completion:
        input_est = self.estimate_input_tokens(request)
        pre = self.meter.preflight(self.model_id, input_est, request.max_tokens, run)
        t0 = time.monotonic()
        response = self.transport.send(request, self.model_id, pre.max_tokens, pre.timeout_s)
        latency_ms = round((time.monotonic() - t0) * 1000)
        if response.replayed and response.recorded_latency_ms is not None:
            # Virtual clock: a replayed call takes as long as it did live, so the seconds cap fires identically.
            run.virtual_seconds += max(0.0, (response.recorded_latency_ms - latency_ms) / 1000)
            latency_ms = response.recorded_latency_ms
        nominal = self.meter.settle(self.model_id, response.usage, run, charged=not response.replayed)
        charged = Decimal(0) if response.replayed else nominal
        log_event(
            log,
            "llm_call_settled",
            purpose=request.purpose,
            model_id=self.model_id,
            tokens_in=response.usage.total_input,
            tokens_out=response.usage.output_tokens,
            cost_usd=str(charged),
            nominal_cost_usd=str(nominal),
            replayed=response.replayed,
            duration_ms=latency_ms,
        )
        return Completion(
            response=response,
            cost_usd=charged,
            latency_ms=latency_ms,
            max_tokens=pre.max_tokens,
            nominal_cost_usd=nominal,
        )


def build_client(
    settings: Settings,
    transport: Transport | None = None,
    record_dir: Path | None = None,
    ledger_engine: Any = None,
) -> LlmClient:
    """Production wiring: replay by default; live only with LLM_MODE=live and a real key. Evals record into
    REPLAY_DIR (committed); the worker passes LLM_RECORD_DIR (gitignored) so real uploads never reach the repo.
    The API passes its app engine as ledger_engine (it never uses the internal role)."""
    from recallops.db.engine import internal_engine

    directory = record_dir or settings.replay_dir
    if transport is None:
        if settings.llm_mode == "replay":
            transport = ReplayTransport(directory)
        elif settings.llm_mode == "live":
            if settings.anthropic_api_key in {"", "sk-ant-fake"}:
                raise LiveCallsDisabled("LLM_MODE=live needs a real ANTHROPIC_API_KEY in .env")
            transport = RecordingTransport(AnthropicTransport(settings.anthropic_api_key), directory)
        else:
            raise LiveCallsDisabled("LLM_MODE=fake needs an injected transport")
    meter = CostMeter(
        prices=PriceTable.from_settings(settings),
        ledger=DbSpendLedger(ledger_engine or internal_engine(), settings.llm_spend_carried_usd),
        budget_stop_usd=settings.llm_budget_stop_usd,
        budget_warn_usd=settings.llm_budget_warn_usd,
        call_timeout_s=settings.llm_timeout_s,
    )
    log_event(log, "llm_client_built", mode=settings.llm_mode, model_id=settings.llm_model_id)
    return LlmClient(transport, meter, settings.llm_model_id, settings)
