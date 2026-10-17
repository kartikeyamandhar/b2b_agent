# M1 runtime trace: live extraction recording run

Observed on 2026-10-17 against api.anthropic.com (owner approved, cap 0.15 USD), model claude-haiku-5-5. Figures come from the agent_runs and agent_steps rows the run wrote (the trace path the meter also reads as its spend ledger).

## Sequence

| ts (UTC) | event |
|---|---|
| 00:45 | Models API lookup (free): claude-haiku-5-5 exists; image input, structured outputs, and disabled thinking supported |
| 00:49:22 | smoke run on notice 24082: one call, 0.000909 USD, recorded; verify_spans dropped correct values because evidence fields were path-qualified (fixed in code, prompt unchanged) |
| 00:50:29 | full run starts: `LLM_MODE=live LLM_BUDGET_STOP_USD=0.15 LLM_MAX_OUTPUT_TOKENS=4096 python -m recallops.evals.extraction` |
| 00:50:30.498 | first notice: load_notice, then draft_rules (3,945 ms, 4,095 tokens in, 1,024 out, 0.000922 USD), verify_spans, finalize |
| 00:50:34.496 | second notice starts about 4 s later |
| 00:53:21 | run ends: 40 runs done, exit 0 |

Per notice the order was always load_notice > draft_rules > verify_spans > finalize (4 agent_steps rows, one model call). No run redrafted, none was capped, none failed. Each step was committed before the next node ran, and the meter's preflight read the spend total, including the previous notice's committed cost, before every call.

## Totals for the 40 calls

- Wall clock 2 min 52 s; draft_rules latency p50 3,966 ms, p90 5,558 ms, max 8,127 ms.
- Tokens: 165,451 in, 41,862 out (largest single response 2,630 tokens, within the 4,096 cap, so nothing was truncated).
- Cost per call 0.000708 to 0.001796 USD; run total 0.037476 USD; ledger after the run (smoke plus run) 0.038389 USD of the 2.00 USD M1 budget.

## Replay check

`make eval` (LLM_MODE=replay) then read the 40 recordings from evals/recordings/ and reproduced the live table exactly (graph 86.6 on all 40, 86.0 on the 25 held-out), with no network calls; the ledger still read 0.038389 USD afterwards.
