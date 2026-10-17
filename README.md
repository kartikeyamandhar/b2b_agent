# RecallOps

Operations console for a company running a U.S. consumer product recall: load a recall from the CPSC feed, turn the notice into machine-checkable rules, adjudicate consumer claims, pay refunds (Stripe test mode), and file monthly progress reports. Team of four, one owner per component; see CLAUDE.md and docs/components/.

This README covers the backend (component 1, apps/api) as of milestone M1. Numbers are in evals/results/.

## Quickstart (backend)

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/) 0.11.21, Python 3.13, make. A machine without the database image pulls it once: `docker pull postgres:16`.

```
git clone https://github.com/kartikeyamandhar/b2b_agent.git recallops && cd recallops
cp .env.example .env
docker compose up -d
make setup
make seed
make dev
```

- `make setup`: installs the pinned dependencies (apps/api/uv.lock), starts Postgres if needed, creates the three database roles and the `recallops` and `recallops_test` databases, and runs the migrations.
- `make seed`: loads the 51-record CPSC fixture, opens a program for the Scotch thermal laminator recall (CPSC 27024) under a demo firm, generates 500 synthetic claims with rendered photos, and adjudicates them with the rules-only adjudicator. About 45 seconds. Ids are deterministic.
- `make dev` (or `make api` without reload): the API on http://127.0.0.1:8000, interactive docs at http://127.0.0.1:8000/docs.

Other targets: `make test`, `make lint`, `make typecheck`, `make check` (all three), `make eval` (rewrites evals/results/*.md in replay mode, no model calls), `make ingest` (loads the saved CPSC archive from data/raw/cpsc, see data/raw/SOURCES.md), `make openapi`, `make label ARGS="--audit"`, `make help`.

## API calls (development auth)

In development, authenticate with headers (AUTH_PROVIDER=dev). Errors are RFC 7807 problem details with a `correlation_id`. Routes marked 501 have a final contract (docs/contracts/openapi.json) and arrive in the named milestone.

```
B=http://127.0.0.1:8000
F=00000000-0000-4000-8000-00000000d001      # demo firm
G=00000000-0000-4000-8000-00000000d002      # other firm (5 claims)
P=0874f66f-bfb5-552b-bd93-6266f49c1d5a      # demo program
C=66dcc492-086f-5b59-b441-cbe001eb2e0b      # a demo claim
A=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: admin")
R=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: reviewer")

# Health
curl -s $B/healthz
curl -s $B/readyz

# Public archive search (full text)
curl -s "${R[@]}" "$B/v1/recalls?q=laminator&limit=5"
curl -s "${R[@]}" "$B/v1/recalls?since=2026-10-01"

# Programs
curl -s "${R[@]}" $B/v1/programs
curl -s "${R[@]}" $B/v1/programs/$P
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"recall_id": null}' $B/v1/programs          # 501, M2
curl -s -X PATCH "${A[@]}" -H 'content-type: application/json' -d '{"status": "closed"}' $B/v1/programs/$P  # 501, M2

# Claims queue and detail
curl -s "${R[@]}" "$B/v1/programs/$P/claims?status=flagged&limit=5"
curl -s "${R[@]}" "$B/v1/programs/$P/claims?limit=50&cursor=<next_cursor from the previous page>"
curl -s "${R[@]}" $B/v1/claims/$C
curl -s "${R[@]}" -o label.jpg "$B$(curl -s "${R[@]}" $B/v1/claims/$C | python3 -c 'import json,sys; print(json.load(sys.stdin)["photos"][0]["url"])')"
curl -s "${R[@]}" $B/v1/claims/$C/trace
curl -s -X POST "${R[@]}" -H 'content-type: application/json' \
  -d '{"action": "request_info", "message": "Please send a clearer label photo"}' $B/v1/claims/$C/actions       # 501, M3
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"n": 10}' $B/v1/programs/$P/claims/issue    # 501, M2

# MPR (M4)
curl -s "${R[@]}" "$B/v1/programs/$P/mpr?period=2026-10"                       # 501
curl -s -X POST "${A[@]}" "$B/v1/programs/$P/mpr/generate?period=2026-10"      # 501
curl -s "${R[@]}" $B/v1/programs/$P/mpr/00000000-0000-0000-0000-000000000000.pdf  # 501

# Usage and billing
curl -s "${R[@]}" $B/v1/usage
curl -s -X POST "${A[@]}" $B/v1/billing/checkout    # 501, payments owner
curl -s -X POST "${A[@]}" $B/v1/billing/portal      # 501, payments owner

# Claim portal (token in the path; dev tokens are dev.<claim id>)
curl -s $B/portal/dev.$C
curl -s -X POST -H 'content-type: application/json' -d '{"name": "Avery Test", "email": "a@example.com", "address_line1": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701", "model_claimed": "TL1302Z"}' $B/portal/dev.$C/submit   # 501, M2
curl -s -X POST -F kind=label -F file=@label.jpg $B/portal/dev.$C/photos      # 501, M2
curl -s -X POST -H 'content-type: application/json' -d '{"message": "Bought it at Amazon"}' $B/portal/dev.$C/answer  # 501, M2
curl -s -X POST -H 'content-type: application/json' -d '{"method": "check_export"}' $B/portal/dev.$C/payout  # 501, M4

# Internal
curl -s -X POST $B/internal/feeds/pull        # 501, M2
curl -s -X POST $B/internal/stripe/webhook    # 501, payments owner

# Tenant isolation: the other firm cannot see the demo firm's claim (404, not 403)
curl -s -H "X-Dev-Firm-Id: $G" $B/v1/claims/$C
```

## Layout

```
apps/api/            FastAPI, LangGraph extraction graph, rules-only adjudicator, ingestion, synthetic claims, evals
  migrations/        Alembic (hand-written SQL: tables, forced RLS, grants)
  sql/               role bootstrap run by make setup
  src/recallops/     settings, db, models, ingest, rules, llm (cost meter), claims, fraud, synth, tracing, auth (login seam),
                     billing and payouts (payments seams), api, evals, seed
  tests/             unit, db (needs make setup), api
evals/               golden sets, pre-registrations, results/
docs/contracts/      openapi.json and trace.md: the contracts the other components code against
docs/onboarding/     ui.md, payments.md, login.md
docs/traces/         runtime traces of observed runs
data/samples/        committed CPSC fixture; data/raw/ is gitignored, provenance in data/raw/SOURCES.md
```

## Results so far (M1)

- Extraction, 40 golden notices (model-labeled, unaudited): regex baseline headline 95.1 on all 40 and 92.9 on the 25 it was not developed on; the model-based extractor (claude-haiku-5-5, one call per notice, 0.037 USD for all 40) scores 86.6 and 86.0. The extraction kill criterion is met, provisionally: the model extractor is demoted to a fallback until the owner-audited 100-notice set in M2 decides. Before span verification the model's drafts score 92.2, so the verbatim-quote rule costs 5.6 points and the model is still 2.9 points behind the baseline without it. Responses are recorded in evals/recordings/, so `make eval` replays them at no cost. evals/results/extraction.md.
- Adjudication, 300 synthetic golden claims: rules-only adjudicator macro F1 0.982, outcome accuracy 0.987, fraud precision and recall 1.000. Its misses are intact-product destruction photos, which need vision. Because the claims are generated from the same rules, rules-only is near its ceiling here; the M2 owner-reviewed and classmate-written claims are where an agent can show value. evals/results/adjudication.md.
- Completion forecast: killed, because the public MPR spreadsheet has one cumulative row per recall per snapshot, not monthly rows (CLAUDE.md decisions log).
