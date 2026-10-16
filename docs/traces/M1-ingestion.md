# M1 runtime trace: CPSC ingestion

Observed on 2026-10-16 against the local Docker Postgres (postgres:16.14) with the recallops_internal role. All timestamps are UTC from the runner's JSON-lines logs; nothing below is a description of intended behavior.

## Run 1: live API pull (network), 2026-09-01 to 2026-10-16

Command: `python -m recallops.ingest.runner --source cpsc --since 2026-09-01 --until 2026-10-16` (run_id ccb76b7a-eb0e-4459-81f2-004de71faa29). The archive was already loaded, so every record compared equal by raw sha256.

| ts | event | detail |
|---|---|---|
| 22:13:57.581 | feed_run_started | feed_runs row inserted with status running |
| 22:13:58.131 | cpsc_window_fetched | 2026-09-01..2026-09-30, HTTP 200, 50 records, 452 ms |
| 22:13:58.332 | feed_window_loaded | 50 fetched, 0 inserted, 0 updated, 50 unchanged, 0 failed, 201 ms (one transaction, one savepoint per record) |
| 22:14:00.714 | cpsc_window_fetched | 2026-10-01..2026-10-16, HTTP 200, 26 records, 372 ms; started after the 2.0 s pause |
| 22:14:00.829 | feed_window_loaded | 26 fetched, 26 unchanged, 114 ms |
| 22:14:00.839 | feed_run_finished | status done, 76 fetched = 0 + 0 + 76 + 0, total 3,258 ms |

Ordering observed: the feed_runs row commits before the first HTTP call, each window's fetch completes before its database transaction opens, the pause sits between windows (not after the last), and the run row is updated last, in a finally block, so a crash mid-run still leaves a finished_at and a status.

## Run 2: offline load of the saved archive (52 windows)

Command: `make ingest` (run_id 59e39d41-d7ca-4c40-a159-6bfb87321d65), empty recalls table.

| ts | event | detail |
|---|---|---|
| 22:13:41.875 | feed_run_started | |
| 22:13:41.928 | feed_window_loaded | 2022-08-01_2022-08-30: 15 inserted, 52 ms |
| 22:13:41.990 | feed_window_loaded | 2022-08-31_2022-09-29: 30 inserted, 61 ms |
| ... | ... | 48 more windows, 41 to 361 ms each |
| 22:13:46.031 | feed_window_loaded | 2026-09-09_2026-10-08: 57 inserted, 361 ms |
| 22:13:46.085 | feed_window_loaded | 2026-10-09_2026-10-16: 6 inserted, 52 ms |
| 22:13:46.094 | feed_run_finished | status done, 1,657 fetched = 1,657 inserted, 0 failed, 4,219 ms |

## Run 3: idempotency (same command again)

run_id c47d52d2-9570-4d2b-b454-472615547bce: 1,657 fetched, 0 inserted, 0 updated, 1,657 unchanged, 0 failed, 1.32 s. The recalls table still holds 1,657 rows with 1,657 distinct source ids.

## feed_runs after the three runs

```
status | fetched | inserted | updated | unchanged | failed | seconds
done   | 1657    | 1657     | 0       | 0         | 0      | 4.21
done   | 1657    | 0        | 0       | 1657      | 0      | 1.32
done   | 76      | 0        | 0       | 76        | 0      | 3.25
```

Not yet traced: the nightly GitHub Actions cron calling POST /internal/feeds/pull (M2), which will append a trace here.
