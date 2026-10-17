"""The meter refuses before the transport is called; refused calls produce zero requests."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from recallops.llm.client import (
    FakeTransport,
    ImageInput,
    LiveCallsDisabled,
    LlmClient,
    ModelRequest,
    ModelResponse,
    ReplayMiss,
    ReplayTransport,
    build_client,
)
from recallops.llm.meter import BudgetExhausted, CostMeter, InMemoryLedger, RunBudget, RunCapExceeded
from recallops.llm.pricing import PriceTable, UnpricedModel, Usage, estimate_image_tokens
from recallops.settings import Settings

MODEL = "claude-haiku-5-5"
PRICES = PriceTable(MODEL, Decimal("0.10"), Decimal("0.50"), 100_000, Decimal("0.50"), Decimal("2.50"))


def response(tokens_in: int = 1000, tokens_out: int = 200) -> ModelResponse:
    return ModelResponse(text="{}", usage=Usage(tokens_in, tokens_out), stop_reason="end_turn", model_id=MODEL)


def client(
    responses: list[ModelResponse], spent: Decimal = Decimal(0), stop: Decimal = Decimal("2.00")
) -> tuple[LlmClient, FakeTransport, InMemoryLedger]:
    transport = FakeTransport(responses)
    ledger = InMemoryLedger(spent)
    meter = CostMeter(PRICES, ledger, budget_stop_usd=stop, budget_warn_usd=Decimal("1.50"))
    return LlmClient(transport, meter, MODEL, Settings()), transport, ledger


def run_budget(usd: str = "0.02", steps: int = 8, seconds: float = 60) -> RunBudget:
    return RunBudget(max_steps=steps, max_usd=Decimal(usd), max_seconds=seconds)


REQUEST = ModelRequest(system="extract", user_text="x" * 4000, max_tokens=2048)


def test_call_goes_through_and_cost_is_settled() -> None:
    llm, transport, ledger = client([response(1000, 200)])
    run = run_budget()
    done = llm.complete(REQUEST, run)
    assert len(transport.calls) == 1
    expected = (Decimal(1000) * Decimal("0.10") + Decimal(200) * Decimal("0.50")) / Decimal(1_000_000)
    assert done.cost_usd == expected == run.cost_usd == ledger.spent
    assert run.steps == 1


def test_max_tokens_is_cut_to_fit_the_run_cap() -> None:
    llm, transport, _ = client([response()])
    run = run_budget(usd="0.0012")
    llm.complete(REQUEST, run)
    sent_max = transport.calls[0][1]
    assert 256 <= sent_max < REQUEST.max_tokens
    worst = PRICES.cost(MODEL, llm.estimate_input_tokens(REQUEST), sent_max)
    assert worst <= Decimal("0.0012")


def test_run_usd_cap_refuses_before_sending() -> None:
    llm, transport, _ = client([response()])
    with pytest.raises(RunCapExceeded) as err:
        llm.complete(REQUEST, run_budget(usd="0.0002"))
    assert err.value.kind == "usd"
    assert transport.calls == []


def test_step_cap_refuses_before_sending() -> None:
    llm, transport, _ = client([response(), response()])
    run = run_budget(steps=1)
    llm.complete(REQUEST, run)
    with pytest.raises(RunCapExceeded) as err:
        llm.complete(REQUEST, run)
    assert err.value.kind == "steps"
    assert len(transport.calls) == 1


def test_wall_clock_cap_refuses_before_sending() -> None:
    llm, transport, _ = client([response()])
    run = run_budget(seconds=0.0)
    with pytest.raises(RunCapExceeded) as err:
        llm.complete(REQUEST, run)
    assert err.value.kind == "seconds"
    assert transport.calls == []


def test_global_budget_exhausted_refuses_with_zero_requests() -> None:
    llm, transport, _ = client([response()], spent=Decimal("2.00"))
    with pytest.raises(BudgetExhausted):
        llm.complete(REQUEST, run_budget())
    assert transport.calls == []


def test_global_budget_nearly_exhausted_refuses_when_unaffordable() -> None:
    llm, transport, _ = client([response()], spent=Decimal("1.99995"))
    with pytest.raises(BudgetExhausted):
        llm.complete(REQUEST, run_budget(usd="1.00"))
    assert transport.calls == []


def test_unpriced_model_fails_closed() -> None:
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1))
    llm = LlmClient(FakeTransport([response()]), meter, "some-other-model", Settings())
    with pytest.raises(UnpricedModel):
        llm.complete(REQUEST, run_budget())


def test_zero_price_fails_closed() -> None:
    free = PriceTable(MODEL, Decimal(0), Decimal(0), 100_000, Decimal(0), Decimal(0))
    with pytest.raises(UnpricedModel):
        free.cost(MODEL, 10, 10)


def test_long_context_uses_long_rates() -> None:
    assert PRICES.rates(MODEL, 200_000) == (Decimal("0.50"), Decimal("2.50"))


def test_estimate_overcounts_and_counts_images() -> None:
    llm, _, _ = client([])
    image = ImageInput("image/jpeg", "abc", 768, 576)
    req = ModelRequest(system="s", user_text="hello world " * 100, images=(image,))
    assert llm.estimate_input_tokens(req) >= 600 + estimate_image_tokens(768, 576, 28)
    assert estimate_image_tokens(768, 576, 28) == 28 * 21


def test_warning_logged_once_when_crossing_threshold(caplog: pytest.LogCaptureFixture) -> None:
    llm, _, _ = client([response(), response()], spent=Decimal("1.4999"))
    caplog.set_level("WARNING", logger="recallops.llm.meter")
    llm.complete(REQUEST, run_budget())
    llm.complete(REQUEST, run_budget())
    assert [r.getMessage() for r in caplog.records].count("llm_budget_warning") == 1


def test_replay_miss_is_an_error_not_a_live_call(tmp_path: object) -> None:
    from pathlib import Path

    transport = ReplayTransport(Path(str(tmp_path)))
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1))
    llm = LlmClient(transport, meter, MODEL, Settings())
    with pytest.raises(ReplayMiss):
        llm.complete(REQUEST, run_budget())


def test_replay_hit_returns_recording(tmp_path: object) -> None:
    import json
    from pathlib import Path

    directory = Path(str(tmp_path))
    key = REQUEST.canonical_key(MODEL)
    recording = {"text": '{"a": 1}', "usage": {"input_tokens": 10, "output_tokens": 5}, "model_id": MODEL}
    (directory / f"{key}.json").write_text(json.dumps(recording))
    llm = LlmClient(
        ReplayTransport(directory),
        CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1)),
        MODEL,
        Settings(),
    )
    assert llm.complete(REQUEST, run_budget()).response.text == '{"a": 1}'


def test_live_mode_refuses_fake_key_and_fake_mode_needs_transport() -> None:
    with pytest.raises(LiveCallsDisabled):
        build_client(Settings(llm_mode="live", anthropic_api_key="sk-ant-fake"))
    with pytest.raises(LiveCallsDisabled):
        build_client(Settings(llm_mode="fake"))


def test_canonical_key_ignores_max_tokens_but_not_content() -> None:
    a = ModelRequest(system="s", user_text="u", max_tokens=10)
    b = ModelRequest(system="s", user_text="u", max_tokens=99)
    c = ModelRequest(system="s", user_text="v")
    assert a.canonical_key(MODEL) == b.canonical_key(MODEL) != c.canonical_key(MODEL)


def test_wire_schema_drops_unsupported_constraints_and_disables_thinking() -> None:
    from recallops.llm.client import api_schema, request_kwargs
    from recallops.rules.schemas import ExtractedRules

    schema = ExtractedRules.model_json_schema()
    assert "minimum" in json.dumps(schema)
    wire = api_schema(schema)
    assert "minimum" not in json.dumps(wire) and "maxLength" not in json.dumps(wire)
    assert '"additionalProperties": false' in json.dumps(wire)
    image = ImageInput("image/jpeg", "abc", 10, 10)
    body = request_kwargs(ModelRequest(system="s", user_text="u", output_schema=schema, images=(image,)), MODEL, 500)
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 500
    assert body["messages"][0]["content"][0]["type"] == "image"
    assert body["output_config"]["format"]["schema"] == wire


def test_recording_transport_output_replays(tmp_path: object) -> None:
    from pathlib import Path

    from recallops.llm.client import RecordingTransport

    directory = Path(str(tmp_path))
    recorder = RecordingTransport(FakeTransport([response(1200, 300)]), directory)
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1))
    live = LlmClient(recorder, meter, MODEL, Settings()).complete(REQUEST, run_budget())
    assert len(recorder.recorded) == 1
    replayed = LlmClient(ReplayTransport(directory), meter, MODEL, Settings()).complete(REQUEST, run_budget())
    assert replayed.response.text == live.response.text
    assert replayed.response.usage == live.response.usage


def test_live_mode_wraps_the_sdk_transport_in_a_recorder() -> None:
    from recallops.llm.client import AnthropicTransport, RecordingTransport

    llm = build_client(Settings(llm_mode="live", anthropic_api_key="sk-ant-" + "x" * 40))
    assert isinstance(llm.transport, RecordingTransport)
    assert isinstance(llm.transport.inner, AnthropicTransport)
