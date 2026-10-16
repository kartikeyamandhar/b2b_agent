# M1 runtime trace: make eval

Observed on 2026-10-16 (`make eval`, LLM_MODE=replay, no model API reachable or approved in session 1).

## Order of operations, as logged

1. `recallops.evals.run_all` configures JSON logging, then runs the extraction eval, then the adjudication eval, then prints a summary.
2. Extraction: the baseline extractor runs on all 40 golden notices from their committed source_text files. The extraction graph is then run through `build_client(settings)`: in replay mode the transport is `ReplayTransport(evals/recordings)`. For each notice the meter's preflight runs and logs `llm_preflight_ok` (projected cost of a call with a conservative token estimate), the transport finds no recording and raises `ReplayMiss`, and the graph ends the run as `failed` without retrying and without counting a model call. Result: 40 runs, outcomes `{failed: 40}`, 0 model calls, 0.000000 USD. The table reports the graph as "not run" rather than scoring 40 empty extractions.
3. Adjudication: the rules-only adjudicator runs twice over the 300 golden claims, once with tools 1.0.1 (receipts matched on exact bytes) and once with tools 1.0.0 (pHash on receipts, the first-run configuration kept for the record). Each claim writes 7 steps to an in-memory trace; per-claim latency median 0.1 ms.
4. Tables are written to evals/results/extraction.md and .json, adjudication.md and .json.

## Observed summary

```
extraction: label_status "model-labeled, unaudited", baseline headline 95.1 (all 40), 92.9 (25 held-out),
            graph "not run", graph_model_calls 0, graph_cost_usd 0.000000
adjudication: n 300, outcome_accuracy 0.9867, macro_f1 0.9817, fraud_precision 1.0, fraud_recall 1.0
```

A second `make eval` produced identical numbers; only the generated-at line changed.

Spend ledger after the run: `sum(agent_steps.cost_usd)` in the dev database is 0.000000 USD, and `LLM_SPEND_CARRIED_USD` is 0, so the meter's global total is 0 of the 2.00 USD M1 budget.
