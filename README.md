# RecallOps

Operations console for a company running a U.S. consumer product recall: load a recall from the CPSC feed, turn the notice into machine-checkable rules, adjudicate consumer claims with a tool-using agent, pay refunds (Stripe test mode), find fraud rings, reach registered owners, and file monthly progress reports. Team of four, one owner per component; see CLAUDE.md and docs/components/.

This README covers the backend (component 1, apps/api), milestones M1 to M5. The UI, payments, and login components are built by their owners against docs/contracts/ (their onboarding is in docs/onboarding/). Numbers are in evals/results/.

## Quickstart (backend)

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/) 0.11.21, Python 3.13, make. A machine without the images pulls them once: `docker pull pgvector/pgvector:0.8.6-pg16 && docker pull neo4j:2026.09.0-community`.

```
git clone https://github.com/kartikeyamandhar/b2b_agent.git recallops && cd recallops
cp .env.example .env
docker compose up -d
make setup
make seed
make dev
```

- `docker compose up -d`: Postgres 16 with pgvector (the system of record) and Neo4j 2026.09.0 Community (the fraud graph), both bound to 127.0.0.1.
- `make setup`: installs the pinned dependencies (apps/api/uv.lock), creates the three database roles and the `recallops` and `recallops_test` databases, and runs the migrations.
- `make seed`: loads the 51-record CPSC fixture, opens a program for the Scotch thermal laminator recall (CPSC 27024) under a demo firm, generates 500 synthetic claims with rendered photos, decides them with the rules-only adjudicator, moves the approved refunds' payouts through the ledger (one export batch included), and builds the fraud clusters in Neo4j. About 45 seconds; ids are deterministic.
- `make dev` (or `make api` without reload): the API on http://127.0.0.1:8000, interactive docs at http://127.0.0.1:8000/docs.
- `make worker`: the adjudication worker. `ADJUDICATOR=rules_only make worker` runs offline at no cost; the agent (`ADJUDICATOR=agent`, the default) needs `LLM_MODE=live` and a key in `.env`, and every call goes through the cost meter (stop at `LLM_BUDGET_STOP_USD`).

Other targets: `make test`, `make lint`, `make typecheck`, `make check` (all three), `make eval` (every eval in replay mode, no network), `make graph-sync` (rebuild the fraud graph from Postgres), `make export-payouts` (write the check/ACH export batches), `make ingest`, `make openapi`, `make label ARGS="--audit"`, `make help`.

## The demo path

```
make seed && make dev                                              # terminal 1
P=0874f66f-bfb5-552b-bd93-6266f49c1d5a
PYTHONPATH=apps/api/src uv run --project apps/api --frozen python -m recallops.synth --program $P --n 20 --seed 7
ADJUDICATOR=rules_only make worker                                 # terminal 2: decides the 20 new claims
```

Then open a claim's trace (`GET /v1/claims/{id}/trace`), the fraud graph (`GET /v1/programs/$P/fraud-graph`), and the MPR (`POST /v1/programs/$P/mpr/generate?period=YYYY-MM`). With `ADJUDICATOR=agent LLM_MODE=live` the same claims are decided by the agent (about 0.0015 USD per claim).

## API calls (development auth)

In development, authenticate with headers (AUTH_PROVIDER=dev). Errors are RFC 7807 problem details with a `correlation_id`. The only 501s left belong to the payments owner (component 3).

```
B=http://127.0.0.1:8000
F=00000000-0000-4000-8000-00000000d001      # demo firm
G=00000000-0000-4000-8000-00000000d002      # other firm (5 claims)
P=0874f66f-bfb5-552b-bd93-6266f49c1d5a      # demo program
C=66dcc492-086f-5b59-b441-cbe001eb2e0b      # a demo claim
T=dev.$F.$C                                 # its portal token (dev tokens are dev.<firm>.<claim>)
A=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: admin")
R=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: reviewer")

# Health (readyz needs a worker heartbeat and reports the graph)
curl -s $B/healthz
curl -s $B/readyz

# Public archive search (full text)
curl -s "${R[@]}" "$B/v1/recalls?q=laminator&limit=5"

# Programs: open one from a recall (rules extracted), then edit and open it
curl -s "${R[@]}" $B/v1/programs
curl -s "${R[@]}" $B/v1/programs/$P
RID=$(curl -s "${R[@]}" "$B/v1/recalls?q=laminator&limit=1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])')
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d "{\"recall_id\": \"$RID\"}" $B/v1/programs

# Claims queue, detail, photo, trace
curl -s "${R[@]}" "$B/v1/programs/$P/claims?status=flagged&limit=5"
curl -s "${R[@]}" $B/v1/claims/$C
curl -s "${R[@]}" $B/v1/claims/$C/trace
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"n": 2}' $B/v1/programs/$P/claims/issue

# Reviewer actions: resume_override, request_info, mark_fraud, mark_delivered
curl -s -X POST "${R[@]}" -H 'content-type: application/json' \
  -d '{"action": "request_info", "message": "Please send a clearer label photo"}' $B/v1/claims/$C/actions

# Fraud graph (Neo4j): rings across every duplicate kind, statuses from Postgres
curl -s "${R[@]}" "$B/v1/programs/$P/fraud-graph?limit=50"

# Payout export batches (the check/ACH fallback)
curl -s -X POST "${A[@]}" $B/v1/payouts/exports
curl -s "${A[@]}" $B/v1/payouts/exports

# MPR: generate (optional manual boxes), read, PDF
PERIOD=$(date -u +%Y-%m)
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"incidents_after": 0}' "$B/v1/programs/$P/mpr/generate?period=$PERIOD"
curl -s "${R[@]}" "$B/v1/programs/$P/mpr?period=$PERIOD"

# Campaigns (M5): synthetic owner registrations, match, drafts, simulated send
PYTHONPATH=apps/api/src uv run --project apps/api --frozen python -m recallops.synth --registrations owners.csv --program $P --n 40
curl -s -X POST "${A[@]}" -F file=@owners.csv $B/v1/programs/$P/registrations
curl -s -X POST "${A[@]}" $B/v1/programs/$P/campaign/match
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"channels": ["email"]}' $B/v1/programs/$P/campaign/drafts
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"channel": "email"}' $B/v1/programs/$P/campaign/send
curl -s "${R[@]}" $B/v1/programs/$P/campaign

# Usage, and the payments owner's stubs
curl -s "${R[@]}" $B/v1/usage
curl -s -X POST "${A[@]}" $B/v1/billing/checkout    # 501 until component 3 lands

# Claim portal (token in the path)
curl -s $B/portal/$T

# Tenant isolation: the other firm cannot see the demo firm's claim (404, not 403)
curl -s -H "X-Dev-Firm-Id: $G" $B/v1/claims/$C
```

## Results

- **Extraction**, 100 golden notices (model-labeled, assistant-audited on the owner's delegation; the 60 blind extension notices decide): regex baseline headline 91.8 on the 60, the model-based extractor (claude-haiku-5-5, one call per notice) 87.9. The extraction kill criterion is met: the model-based extractor is a fallback. Before span verification its drafts reach 92.7, so even relaxed verification would not close the gap. evals/results/extraction.md.
- **Adjudication ablation** (macro F1, evals/results/ablation.md), every row replayed from committed recordings:

| row | golden300 | probe60 | standin40 | mean cost per claim |
|---|---|---|---|---|
| rules_only | **0.982** | 0.095 | 0.583 | 0 |
| rules_plus_vision | 0.933 | **0.726** | 0.668 | 0.0002 USD |
| single_pass | 0.919 | 0.663 | 0.710 | 0.0009 USD |
| loop_full (the agent) | 0.916 | 0.703 | **0.774** | 0.0016 USD |
| loop_no_retrieval | 0.908 | 0.699 | 0.718 | 0.0015 USD |
| loop_keyword_only | 0.915 | 0.691 | 0.774 | 0.0016 USD |

  The adjudication kill criterion fires: on the 300 synthetic golden claims, which are generated from the same rules, rules_only leads the agent by 0.066. The agent's value is where rules cannot see: label photos that contradict or complete the typed model (probe60, an assistant-designed probe of rules-only blind spots), and claims written without the generator (standin40, an agent-written stand-in for the planned classmate set), plus fraud routing. Two pre-registered expectations missed and are reported as such: the photo-reading rows score below 0.75 on probe60, because the vision check rejects the synthetic compliant destruction photos as evidence, and retrieval helps by 0.056 on standin40 where it was expected to be neutral (evals/adjudication/PREREG.md).
- **Fraud graph** (Neo4j projection): on the 300 golden claims it reproduces exactly the 36 duplicate components of check_duplicates (13 serial, 12 payout, 7 photo, 4 address) and 36 rings; two firms with identical photos and addresses never share a node.
- **MPR reconciliation**: the SQL report, an independent Polars recount, and the scenario builder's own truth agree on every computed box in every month (corrections, contacts, units, notified), with zero discrepancies; mutation tests prove a month-boundary or row-counting bug fails it. evals/results/reporting.md.
- **Spend**: 1.62 USD of model calls for the whole project so far (extraction recordings, the full ablation, and the live runtime trace), under the 8 USD stop.

## Deploying (not done here)

infra/render.yaml deploys the API with `APP_ENV=prod`, so the header-trusting dev auth provider can never run publicly: the API fails closed until the login owner's Clerk provider and HMAC claim tokens land. The worker is a separate service (it holds the BYPASSRLS role; the web process never does). The fraud graph uses Neo4j AuraDB Free in deploy (`NEO4J_URI=neo4j+s://...`, `NEO4J_NAMESPACE=prod`, a real `GRAPH_KEY_SECRET`); prod settings refuse the development values. Credentials live only in the deploy environment, never in `.env` files in this repository.

## Layout

```
apps/api/            FastAPI, the LangGraph adjudication agent and worker, rules extraction, ingestion, payouts
                     ledger and export, MPR, campaigns, the Neo4j fraud graph, synthetic claims, evals
  migrations/        Alembic (hand-written SQL: tables, forced RLS, grants, triggers)
  sql/               role bootstrap run by make setup
  src/recallops/     settings, db, models, ingest, rules, llm (cost meter), claims, fraud, payouts, mpr, campaigns,
                     retrieval, tracing, auth (login seam), billing (payments seam), api, evals, synth, seed
  tests/             unit, db (needs make setup), api
evals/               golden sets, pre-registrations, recordings (replay), results/
docs/contracts/      openapi.json, trace.md, CHANGES.md: what the other components code against
docs/onboarding/     ui.md, payments.md, login.md
docs/traces/         observed runtime traces, one file per flow per milestone
infra/               render.yaml
data/samples/        committed CPSC fixture; data/raw/ is gitignored, provenance in data/raw/SOURCES.md
```
