# Trace contract: agent runs and steps

Owner: backend. Consumer: the UI trace viewer (`/app/claims/[id]/trace`, 02-ui.md component 6). Source of truth: the `agent_runs` and `agent_steps` tables (CLAUDE.md invariant 3: there is no second logging path). The API serves them at `GET /v1/claims/{claim_id}/trace` (schema `Trace` in docs/contracts/openapi.json). Changing a field or a vocabulary value below needs a decisions-log entry and a message to the UI owner before the pull request merges.

## Response shape

```json
{
  "run": {
    "run_id": "uuid",
    "graph": "rules_only",
    "status": "done",
    "steps": 7,
    "cost_usd": "0.000000",
    "started_at": "2026-10-16T22:40:04.120Z",
    "finished_at": "2026-10-16T22:40:04.135Z"
  },
  "caps": {"max_steps": 8, "max_usd": "0.02", "max_seconds": 60.0},
  "used": {"steps": 0, "cost_usd": "0.000000", "seconds": 0.002},
  "steps": [
    {
      "idx": 0,
      "node": "check_model",
      "thought": null,
      "tool": "check_model",
      "args": {"model_claimed": "TL4302Z"},
      "result": {"verdict": "mismatch", "cited_rule": "TL1302Z"},
      "latency_ms": 0,
      "tokens_in": 0,
      "tokens_out": 0,
      "cost_usd": "0.000000",
      "created_at": "2026-10-16T22:40:04.122Z",
      "counted": false,
      "nominal_cost_usd": "0.000000"
    }
  ]
}
```

## Fields

| field | type | meaning |
|---|---|---|
| run.run_id | uuid | the agent_runs row; also the LangGraph thread id |
| run.graph | enum | `extract_rules`, `adjudicate` (the M2 agent), `rules_only` (M1 baseline), `rules_plus_vision` (eval row) |
| run.status | enum | see the run status vocabulary below |
| run.steps | int | rows written so far (every node writes one); not the cap count, see used.steps |
| run.cost_usd | decimal string | sum of step costs in USD, six decimals; never a float |
| caps | object | the per-run caps the header bar shows (invariant 4): 8 steps, 0.02 USD, 60 seconds |
| used | object | what the run used, for the header bar against caps: `steps` (counted rows), `cost_usd` (nominal, decimal string), `seconds` (sum of step latencies) |
| steps[].idx | int | 0-based order within the run, unique per run |
| steps[].node | string | the graph node that wrote the row |
| steps[].thought | string or null | model reasoning summary when a model ran; null for pure-code tools |
| steps[].tool | string or null | tool name; null for nodes that call no tool |
| steps[].args | object or null | the tool's validated arguments (pydantic) |
| steps[].result | object or null | the tool's typed result; on refusal it contains `capped` or `error` |
| steps[].latency_ms | int | wall time of the step |
| steps[].tokens_in, tokens_out | int | model tokens; 0 for pure-code steps |
| steps[].cost_usd | decimal string | cost charged to the budget for this step in USD; 0 when a recorded response was replayed |
| steps[].nominal_cost_usd | decimal string | cost from the reported usage, live or replayed; `used.cost_usd` sums these |
| steps[].counted | bool | true when the step counts toward caps.max_steps: a model call (agent turn or vision tool) or a rejected tool call |
| steps[].created_at | timestamp | when the row was written (UTC) |

## Run status vocabulary

| status | meaning | UI treatment |
|---|---|---|
| running | steps are still being written | poll GET /v1/claims/{id}/trace every 1 to 2 s while running (a claim takes about 10 to 20 s), then stop |
| done | the run finished and wrote its outcome | final |
| capped | a cap (steps, USD, or seconds) stopped the run before a model call; the claim waits for a reviewer | warning badge; show which cap from the last step's `result.capped` |
| failed | the run ended without an outcome (for example a model error or a missing replay recording) | error badge with the last step's `result.error` |
| waiting_human | a LangGraph interrupt is waiting for a reviewer (flagged, or an approval over the value threshold) | action prompt; POST /v1/claims/{id}/actions queues the review and the worker resumes the same run (same run_id, a `human_review` row is appended) |

capped and failed runs of graph `adjudicate` also wait for a reviewer the same way (the claim status is `capped`). After a review, the run's status becomes `done`.

## Node names by graph

- rules_only (M1): `check_model`, `check_serial`, `check_date_code`, `check_purchase`, `check_destruction_presence`, `check_duplicates`, `decide`. Always 7 steps, cost 0.
- extract_rules (M1): `load_notice`, `draft_rules` (one or two), `verify_spans`, `finalize`.
- adjudicate (M2), loop variants: `load_context`, then one `agent` row per model turn (tool `llm`; `thought` is the model's one-sentence reason for its next checks), one row per tool call (`read_label_photo`, `check_model`, `check_serial`, `check_date_code`, `check_purchase`, `check_destruction_photo`, `check_duplicates`, `retrieve_policy`; each result carries `result_id` T1, T2, ... that the decision cites), a `decide` row (accepted or the validation error), `route`, and `human_review` after a reviewer resumes the run. Rejected tool calls are rows whose `result.error` says why.
- adjudicate, single_pass variant: `load_context`, every tool once in a fixed order, `decide_once`, `route`, `human_review`.
- rules_plus_vision (eval row): the rules_only nodes plus `read_label_photo` and `check_destruction_photo`.

## Claim status vocabulary (for the queue)

`draft`, `submitted`, `needs_info`, `under_review` (approved but routed to a human, for example over the value threshold), `approved`, `denied`, `flagged`, `capped` (the checker stopped on a cap or a failure; a person decides), `paid`, `closed`.

Changes on 2026-10-18 (additive, decisions log): `used`, `steps[].counted`, `steps[].nominal_cost_usd`, the `rules_plus_vision` graph value, the adjudicate node names, and the 1 to 2 s polling guidance while a run is running. The adjudication outcome in `decision.outcome` is one of `approve`, `deny`, `needs_info`, `flag`.
