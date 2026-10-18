# M2 runtime trace: live extraction recording run on the 60 extension notices

Observed on 2026-10-18 against api.anthropic.com, model claude-haiku-5-5, prompt sha256 7dc29f49 (unchanged since M1). The run cap was LLM_BUDGET_STOP_USD=0.25 for this run, and the output limit was LLM_MAX_OUTPUT_TOKENS=4096, as in M1. The command ran all 100 golden notices through the replay-first live transport: the 40 M1 notices were answered from their recordings, and only the 60 new ones called the API. Figures come from the agent_runs and agent_steps rows the run wrote, which are also the meter's spend ledger.

## Sequence

| ts (UTC) | event |
|---|---|
| 23:09:24 | `LLM_MODE=live LLM_BUDGET_STOP_USD=0.25 LLM_MAX_OUTPUT_TOKENS=4096 python -m recallops.evals.extraction` starts; ledger reads 0.038389 USD |
| 23:09:25.680 | first notice (24080, new): load_notice, then draft_rules, a live call (21,036 ms, the slowest of the run: the first call opens the connection), then verify_spans and finalize |
| 23:09:46.780 | 24081 (new): draft_rules 4,560 ms |
| 23:09:51.396 | 24082 (M1): draft_rules answered from its recording in 1 ms and charged 0 USD |
| 23:13:32.487 | last run ends: 100 runs, all status done, exit 0 |

Every notice ran load_notice > draft_rules > verify_spans > finalize: 4 agent_steps rows and one model call (live or replayed). No run redrafted, was capped, or failed. Each step was committed before the next node ran, and the meter's preflight read the spend total, including the previous live call's committed cost, before every live call.

## The 60 live calls

- Wall clock for the whole command: 4 min 7 s. Live draft_rules latency: p50 3,695 ms, p90 4,560 ms, max 21,036 ms.
- Tokens: 248,265 in, 60,896 out. The largest single response was 2,103 tokens, under the 4,096 cap, so nothing was truncated.
- Cost per live call: 0.000691 to 0.001508 USD. Charged for the run: 0.055277 USD. Ledger after the run: 0.093666 USD.
- The 40 replayed calls were charged 0 USD. Their nominal cost (0.037476 USD, the M1 recording) still counts in the eval's cost column, which reads 0.092751 USD for all 100.

## Replay check

`make eval` (LLM_MODE=replay) then read the 100 recordings from evals/recordings/ with no network calls and reproduced the live table exactly: on the 60 blind notices, graph 87.9 and baseline 91.8. The ledger still read 0.093666 USD afterwards. The new recordings also store each call's live latency, so a replayed run's seconds cap uses the same virtual clock as the live run.
