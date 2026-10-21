# M6 runtime trace: the README executed from a clean clone

Observed on 2026-10-19 (UTC) on macOS with Docker Desktop, uv 0.11.21, Python 3.13. The repository was cloned fresh (commit 443d2f7), and the README's quickstart ran verbatim with fresh database volumes. The only departure was the environment variable `COMPOSE_PROJECT_NAME=recallops_clean`, so the run could not reuse the development volumes. The clone had no data/generated directory, so no rendered golden photos were present.

| step | result |
|---|---|
| `cp .env.example .env`, `docker compose up -d` | Postgres (pgvector 0.8.6-pg16) and Neo4j 2026.09.0 started |
| `make setup` | 10.3 s; both databases bootstrapped and at migration 0005_m5_campaigns |
| `make seed` | 51.2 s; 500 claims (182 approved, 94 denied, 107 needs_info, 117 flagged); payouts exported 56 (one batch), transferred 54, pending 36, onboarding 18, failed 18; graph ok, 58 clusters (first projection into an empty Neo4j: 4.1 s) |
| `make dev` | API up in 2 s |
| README curl calls | healthz 200; readyz 503 (no worker heartbeat yet, as documented); recall search 27024; program create 201; flagged queue 5; claim detail with 3 photos; photo bytes 200; trace rules_only 7 steps; link issue 200; reviewer action 200; fraud graph 49 nodes and 24 rings (truncated at limit 50); export 200 with 1 batch listed; MPR generated and reconciled (corrections 110, contacts 500); registrations CSV of 40 uploaded (40 accepted), 33 matched, 33 email drafts, 33 sent; usage 200; billing checkout 501 (payments owner); portal view 200; the other firm's claim 404 |
| `make test` | 349 passed, coverage 92 percent |
| `make eval` | every ablation row replayed with the same macro F1 as the committed table (golden300 rules_only 0.9817, loop_full 0.9162), reconciliation PASS with the pre-registered composition, without the photo files |
| `python -m recallops.evals.gate --baseline-ref HEAD --self-ref HEAD` | PASS: the committed tables equal the replay |

Afterwards the scratch compose project and its two volumes were removed (`docker compose down -v` under the same project name), and the development stack was restarted on its own volumes.

## Rerun with the website (2026-10-21, before PR 9 merged)

A fresh clone of `christina/frontend-ui` at d355ec3 into a scratch folder, with `COMPOSE_PROJECT_NAME=recallops-clean` so it got its own empty volumes, following the README exactly. Docker images and the uv and npm caches were already on this machine, so the install times are warm-cache times.

| step | observed |
|---|---|
| `cp .env.example .env`, `docker compose up -d --wait` | Postgres and Neo4j healthy in 9.95 s |
| `make setup` | 9.85 s: uv sync (84 packages), `bootstrapped recallops`, `bootstrapped recallops_test`, migrations to head, then `make web-setup`: npm ci (187 packages) and `apps/web/.env.local` created |
| `make seed` | 55.27 s; 500 claims; fraud graph rebuilt |
| `make dev`, `make web` | API on 127.0.0.1:8000, console on localhost:3000; `/app/programs` server-rendered the laminator program |
| dev-mode host check | `Host: 192.168.1.20:3000` answered 403 |
| headers on `/claim/x` | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow` |
| `GET /v1/programs/{P}/fraud-graph` | 58 rings over 117 claims |
| README third terminal: `synth --n 20`, then `ADJUDICATOR=rules_only make worker ARGS=--once` | 22 claims decided (the 20 new ones and 2 earlier ones their duplicates reopened); 520 claims, 0 left submitted |
| `make check` | lint clean, mypy clean (176 files), 521 passed, 1 skipped (it needs the gitignored CPSC photo files), 90% coverage |
| `make web-check` | typecheck clean, 548 tests passed, generated types match the contract |

Found: `/healthz` reported `"graph":"disabled"` although the graph worked. `/healthz` never contacts the graph, and the field's default is "disabled"; `/readyz` reports the real status. `docs/DEPLOY.md` now says so. The clone's containers and volumes were removed afterwards (`docker compose down -v` on the scratch project only).
