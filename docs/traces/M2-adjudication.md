# M2 runtime trace: one claim through every adjudication path

Observed on 2026-10-19 (UTC) against local Docker Postgres 16.15 with the API (`uvicorn`, dev auth) on port 8010 and the worker (`python -m recallops.claims.worker --once`, ADJUDICATOR=agent, LLM_MODE=live, claude-haiku-5-5, variant loop_full). Firm "Trace Demo" (`00000000-0000-4000-8000-0000000007a1`, never wiped by `make seed`, so its spend stays in the ledger), program `bdef6511`, recall 27024. The driver script filed claims through the portal with rendered photos and called the reviewer route; every row below comes from the worker's JSON-lines log, agent_runs, agent_steps, or audit_log. Nothing is intended behavior; it is what was recorded.

Claims: C1 approve (correct label, cut cord, receipt), C2 deny (label shows TL9000Q), C3 needs_info (no destruction photo), C4 a later duplicate of C1 (same three photos, filed after C1 was approved), C5 capped (worker started with RUN_CAP_USD=0.0004).

## Timeline

| ts (UTC) | source | event |
|---|---|---|
| 06:35:28.168 | audit | program_created (baseline extractor, 8 document chunks) |
| 06:35:28.207 | audit | program_status_changed to open |
| 06:35:28.351 to .525 | audit | claim_submitted C1, C2, C3 (portal, uploads first) |
| 06:35:29.670 | worker | worker_run_start C1 (run 2fbce049) |
| 06:35:39.712 | audit | payout_created (pending, stripe_connect) and claim_adjudicated C1 approved: one transaction |
| 06:35:39.740 | worker | worker_claim_done C1 approve, 10,622 ms |
| 06:35:49.019 | worker | worker_claim_done C2 deny, 9,277 ms (label model TL9000Q, model check mismatch) |
| 06:35:55.972 | worker | worker_claim_done C3 needs_info, 6,949 ms (destruction photo missing) |
| 06:35:56.321 | audit | claim_submitted C4 (C1's photos) |
| 06:35:57.544 | worker | worker_run_start C4 (run f8fb421f) |
| 06:36:02.126 | audit | in one transaction: claim_held C4 flagged, claim_reopened_duplicate C1 (approved to submitted), payout_held C1 (duplicate_found_later) |
| 06:36:02.165 | worker | worker_claim_done C4 flag (run waiting_human) |
| 06:36:02.196 | worker | worker_run_start C1 again (new run 99e65410): 70 ms after the reopen committed |
| 06:36:06.920 | audit | claim_held C1 flagged (run waiting_human) |
| 06:36:07.141 | audit | review_submitted C4 resume_override deny (stored as the run's resume_payload) |
| 06:36:07.152 | audit | review_submitted C1 resume_override approve |
| 06:36:08.034 | audit | claim_reviewed C4 denied: resume of run f8fb421f (same run id, one new step: human_review) |
| 06:36:08.071 | audit | claim_reviewed C1 approved and payout_released C1 (held to pending): a person's approval, one transaction |
| 06:36:08.323 | audit | claim_submitted C5 |
| 06:36:09.200 | audit | claim_held C5 capped: "usd cap: 0 of 0.0004 USD used; cannot afford a call" |
| 06:36:09.212 | worker | worker_claim_done C5 capped, 402 ms, no model call made |

## Runs

| claim | run | final run status | trace rows | charged USD | path |
|---|---|---|---|---|---|
| C1 | 2fbce049 | done | 11 | 0.001662 | approve |
| C2 | 2583cddd | done | 11 | 0.001707 | deny |
| C3 | 8d3a76ea | done | 11 | 0.001473 | needs_info |
| C4 | f8fb421f | done after review | 12 (with human_review) | 0.001005 | flag, interrupt, reviewer deny |
| C1 | 99e65410 | done after review | 12 (with human_review) | 0.001007 | reopened, flag, interrupt, reviewer approve |
| C5 | a8dbe2a5 | capped | 3 | 0 | the meter refused the first call |

Step order of a full run (C1, run 2fbce049, latency ms): load_context 0, agent (model) 2,113, read_label_photo 2,278, check_purchase 0, check_duplicates 8, check_destruction_photo 1,266, agent 1,400, check_model 0, agent 2,745, decide 0, route 0. C4's first model call and both vision calls were answered from C1's recordings (identical requests), which is why C4 cost 0.001005 instead of about 0.0017.

## What the trace shows

- The approval and its payout row commit together (06:35:39.712, same transaction).
- A duplicate found after an approval reopens the earlier claim and holds its payout inside the decision's own transaction (06:36:02.126); the worker leases the reopened claim on its next loop and the adjudicator flags it through the interrupt. Both claims end flagged with a run waiting for a person (01-backend.md section 12), and no money can move in between.
- A reviewer's action resumes the same checkpointed run (run ids f8fb421f and 99e65410 unchanged; one human_review step each).
- Only a person's approval releases a held payout (06:36:08.071).
- The cost cap stops a run before any call that would cross it; the claim is capped and routed to a person.

Total model spend of this trace: 0.006854 USD; ledger after the trace 1.620841 USD (stop 4.00).

Known cosmetic defect: the capped detail repeats its prefix ("usd cap: usd cap: ..."). It is composed in claims/graph.py, which is frozen with the ablation (evals/adjudication/freeze.json), so it is logged rather than changed.
