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
      "created_at": "2026-10-16T22:40:04.122Z"
    }
  ]
}
```

## Fields

| field | type | meaning |
|---|---|---|
| run.run_id | uuid | the agent_runs row; also the LangGraph thread id |
| run.graph | enum | `extract_rules`, `adjudicate` (M2 agent), `rules_only` (M1 baseline) |
| run.status | enum | see the run status vocabulary below |
| run.steps | int | steps written so far; compare with caps.max_steps |
| run.cost_usd | decimal string | sum of step costs in USD, six decimals; never a float |
| caps | object | the per-run caps the header bar shows (invariant 4): 8 steps, 0.02 USD, 60 seconds |
| steps[].idx | int | 0-based order within the run, unique per run |
| steps[].node | string | the graph node that wrote the row |
| steps[].thought | string or null | model reasoning summary when a model ran; null for pure-code tools |
| steps[].tool | string or null | tool name; null for nodes that call no tool |
| steps[].args | object or null | the tool's validated arguments (pydantic) |
| steps[].result | object or null | the tool's typed result; on refusal it contains `capped` or `error` |
| steps[].latency_ms | int | wall time of the step |
| steps[].tokens_in, tokens_out | int | model tokens; 0 for pure-code steps |
| steps[].cost_usd | decimal string | cost of this step in USD |
| steps[].created_at | timestamp | when the row was written (UTC) |

## Run status vocabulary

| status | meaning | UI treatment |
|---|---|---|
| running | steps are still being written | poll every 10 s while the tab is visible |
| done | the run finished and wrote its outcome | final |
| capped | a cap (steps, USD, or seconds) stopped the run before a model call; the claim waits for a reviewer | warning badge; show which cap from the last step's `result.capped` |
| failed | the run ended without an outcome (for example a model error or a missing replay recording) | error badge with the last step's `result.error` |
| waiting_human | a LangGraph interrupt is waiting for a reviewer (flagged, over the value threshold, or capped) | action prompt; resuming continues the same run (M2) |

## Node names by graph

- rules_only (M1): `check_model`, `check_serial`, `check_date_code`, `check_purchase`, `check_destruction_presence`, `check_duplicates`, `decide`. Always 7 steps, cost 0.
- extract_rules (M1): `load_notice`, `draft_rules` (one or two), `verify_spans`, `finalize`.
- adjudicate (M2): `load_context`, `read_label_photo`, `check_model`, `check_serial`, `check_date_code`, `check_purchase`, `check_destruction_photo`, `check_duplicates`, `retrieve_policy`, `decide`, `route`. Exact list confirmed in the M2 trace (docs/traces/M2-adjudication.md).

## Claim status vocabulary (for the queue)

`draft`, `submitted`, `needs_info`, `under_review` (approved but routed to a human, for example over the value threshold), `approved`, `denied`, `flagged`, `capped`, `paid`, `closed`. The adjudication outcome in `decision.outcome` is one of `approve`, `deny`, `needs_info`, `flag`.
