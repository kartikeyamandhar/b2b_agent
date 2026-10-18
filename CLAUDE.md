# RecallOps CLAUDE.md

Read this file in full at the start of every session. Then read the component spec for the phase you are in (docs/components/01-backend.md, 02-ui.md, 03-payments.md, 04-login.md). Do not read all four unless the task spans components.

## Intent

RecallOps is the operations console for a company running a product recall. In the United States, a firm that recalls a consumer product must notify owners, verify every consumer claim, deliver a refund, repair, or replacement, and file a monthly progress report (MPR) with the Consumer Product Safety Commission (CPSC) until CPSC accepts the correction rate. Most recalling firms are importers and mid-size brands with no system for this; they run it on a hotline, a spreadsheet, and a law firm.

RecallOps does four things:
1. Loads a recall from the public CPSC feed and turns the free-text notice into machine-checkable identification and remedy rules.
2. Takes consumer claims through a public portal and adjudicates each one with a tool-using agent: model confirmation from the label photo, destruction-photo compliance, proof of purchase against the sold-at list, serial or date-code range checks where the notice has them, duplicate and fraud detection across all claims.
3. Pays approved refunds (Stripe, test mode) and tracks repairs and replacements.
4. Generates the MPR from the claims table each month and forecasts remedy completion against public completion curves.

Why it exists: a graded class project (team of four, one owner per component: backend, UI, payments, login; UCLA MSBA, fall 2026) that must ship a product with a ReAct-style agent loop, retrieval, a modern agent framework, login, and payments. It is judged by a grader first and by anyone who skims the repo second. The thing that distinguishes it is numbers: extraction accuracy, adjudication accuracy, fraud precision and recall, MPR reconciliation at zero discrepancy, and an ablation table that shows which parts of the agent earned their place.

Non-goals, explicit:
- No consumer-facing recall alert app. That market is saturated (RecallSentry, RecallDefense, CoverKeep, Sustain).
- No automotive or dealer-network recall workflows (Tavant owns that).
- No real money. Stripe runs in test mode for the life of the project.
- No real consumer data. Claims are synthetic, generated from real notices with planted violations.
- No employer data or context of any kind enters this repository.
- No Neo4j. Duplicate and fraud clustering runs in Postgres plus networkx.
- No multi-region, no SSO, no SOC2 theater. Tenant isolation is real; compliance paperwork is not.

## Mode

INTERACTIVE. Every phase starts with a plan, a red-team pass on that plan, and an explicit "go" from the owner before implementation. FREE-HAND requires an explicit grant per session and is revoked by any deviation from expectations. In FREE-HAND, execution assumptions go in the chat deliverable, not in a repository file.

Command authorization: running repository code (tests, builds, installs, migrations against the local database) needs the owner's request or first-run approval in a session. Network calls to third parties, credentials, cloud deploys, publication, anything externally visible, and destructive actions always need explicit approval. A flag in a command is not approval.

Sub-agents: at most 3 sub-agents per task without the owner's approval.

Scope of this file's owner: the backend owner writes apps/api and evals/ and owns this file's backend sections. The UI, payments, and login owners work in their own components against the contracts in docs/contracts/ (see "Working in a team").

## Stack (pinned 2026-10-16; upgrade only by logged decision)

Backend (apps/api):
- Python 3.13, managed with uv; lockfile uv.lock committed.
- FastAPI 0.143.3, uvicorn 0.54.0
- LangGraph 1.2.15, langgraph-checkpoint-postgres 3.2.0, langchain-core 1.6.12, langchain-anthropic 1.7.6
- SQLAlchemy 2.1.5, Alembic 1.20.0, psycopg[binary] 3.3.6 (psycopg-binary 3.3.6, added 2026-10-17), pgvector 0.5.1
- Polars 2.0.0 for every tabular job (ingestion, evals, MPR aggregation). No pandas.
- pydantic 2.14.1, pydantic-settings 2.16.0, httpx 0.28.1, PyJWT 2.15.1, networkx 3.7, python-multipart 0.0.32
- stripe 16.0.0 (Python SDK)
- anthropic 1.15.0 (direct SDK for vision calls that bypass LangChain)
- Tooling: ruff 0.17.1 (lint and format), mypy 2.4.0 strict, pytest 9.1.1
- Added 2026-10-16 (session 1, owner-approved): fastexcel 0.21.0 (Polars xlsx engine), Pillow 12.3.0 (synthetic photo rendering), numpy 2.5.4 (pHash DCT and image arrays), PyYAML 6.0.3 (planted-violation config), pytest-cov 7.1.0 (coverage)

Frontend (apps/web):
- Node 22.22.0, npm 10.9.4; package-lock.json committed.
- Next.js 16.4.2 (App Router), React 19.3.1, TypeScript 7.0.2 strict
- Tailwind CSS 4.3.3
- @clerk/nextjs 7.9.17, @clerk/backend 3.25.0
- stripe 23.0.0 (Node SDK, server routes only), @stripe/stripe-js 10.1.0
- @supabase/supabase-js 2.118.0 (storage uploads from the claim portal only)
- zod 4.6.7, @tanstack/react-query 5.104.3
- vitest 5.0.5, playwright 1.64.1

Infrastructure:
- Postgres 16 with pgvector, hosted on Supabase free tier; local development via Docker Compose (docker-compose.yml at the repository root, pgvector/pgvector:pg16 image).
- Supabase Storage for claim photos (private bucket, signed URLs).
- Clerk for firm users and organizations. Consumers never get accounts.
- Stripe test mode: Billing for firm subscriptions, Connect Express for claimant payouts.
- Vercel for apps/web, Render free tier for apps/api, GitHub Actions cron for the nightly feed pull.
- LLM: one cheap, vision-capable model, chosen at phase 0 by checking the provider's current price list, set in config as LLM_MODEL_ID, and recorded in the decisions log below. Never hardcode a model id in source. Embeddings: EMBEDDING_MODEL_ID and EMBEDDING_DIM are chosen with LLM_MODEL_ID at phase 0 and logged; the document_chunks table is created in M2.

Budget: under 20 USD total for the life of the project. Model spend is metered per run and shown in the console. If cumulative model spend passes 8 USD, stop and report. M1 model budget: 2 USD total. Every model call goes through the llm/ wrapper, whose meter checks projected cost before the call; nobody runs a model-calling eval any other way.

## Architecture invariants

Decided. Do not re-litigate silently. Propose a change only with evidence, in the decisions log.

1. Monorepo layout:
   ```
   recallops/            (git repository kartikeyamandhar/b2b_agent)
     CLAUDE.md
     Makefile             setup, api, seed, test, lint, typecheck, eval
     docker-compose.yml   local Postgres 16 + pgvector
     .github/workflows/   ci.yml (M1), deploy.yml (M4)
     .github/CODEOWNERS
     docs/components/01-backend.md 02-ui.md 03-payments.md 04-login.md
     docs/contracts/      openapi.json, trace.md (backend-owned contracts for the team)
     docs/onboarding/     ui.md, payments.md, login.md
     docs/traces/         runtime traces, one file per flow, prefixed by milestone
     docs/KICKOFF_PROMPT.md
     apps/api/        FastAPI + LangGraph + ingestion + evals
     apps/web/        Next.js console and claim portal
     evals/           golden sets (JSON), runners, results/ (committed tables)
     data/raw/        downloaded feeds, gitignored except SOURCES.md (provenance)
     data/samples/    small committed fixtures for tests
     infra/           render.yaml only
   ```
2. Every tenant table carries firm_id. The non-tenant tables are exactly recalls, feed_runs, stripe_events, and worker_heartbeats (no claim data), plus the LangGraph checkpointer tables in schema langgraph, which only recallops_internal can read; firms is scoped by its own id. agent_runs and agent_steps rows of public-archive extraction runs are the only tenant-table rows with firm_id NULL. Every read and write goes through a TenantScoped repository that takes firm_id from the verified token, never from the request body. Postgres row-level security is enabled and FORCED on every tenant table as a second guard, using set_config('app.firm_id', <id>, true) per transaction (the SET LOCAL equivalent that accepts a bound parameter). Database roles: a migration role (recallops_owner) owns every table; recallops_app (NOSUPERUSER, NOBYPASSRLS, owns nothing) is the only role the API uses; recallops_internal (BYPASSRLS) is used by jobs (ingestion, worker, seed, evals). The tenancy test connects as recallops_app, asserts it is neither superuser nor owner of any table, and then proves that firm A's context cannot read, update, or delete firm B's rows.
3. The agent loop is LangGraph with the Postgres checkpointer. Every node writes one row to agent_steps (thought, tool, arguments, result, latency, tokens, cost). The trace viewer in the UI reads that table; there is no second logging path.
4. Hard caps per claim run: 8 steps, 0.02 USD, 60 seconds wall clock. Hitting a cap is a logged outcome (status: capped) and routes to human review. Never a crash.
5. Tools are typed with pydantic models for arguments and results. A tool call with invalid arguments is rejected before execution and counts as a step.
6. Human-in-the-loop is a LangGraph interrupt. Flagged claims, claims over a value threshold, and claims that hit a cap stop and wait for a reviewer. Resuming is a resume of the same checkpointed run, not a new run.
7. Retrieval split: recall notices and identification rules use Postgres full-text search (tsvector, keyword ranking), because model numbers are tokens, not meanings. Prose documents (corrective action plan, warranty terms, FAQs) use pgvector embeddings. Asset and claim history is a direct lookup. The ablation table must include the loop with retrieval turned off.
8. Stripe webhooks are idempotent by event id: insert into stripe_events before processing, inside the same transaction as the state change. A replayed event is a no-op.
9. Consumer payouts are a ledger first. A payouts row exists before any Stripe transfer is attempted; the transfer carries the payouts row id as its idempotency key. If Connect onboarding is not ready, the row stays in status "pending_export" and the export file is the fallback.
10. The claim portal uses signed, expiring, single-claim tokens (HMAC, 90 days). No consumer accounts, no passwords, rate-limited by token and IP.
11. Polars for all tabular work. SQL for aggregation that belongs in the database (MPR counts). No pandas anywhere.
12. Configuration from environment via pydantic-settings. No secrets in source, in tests, or in fixtures. A committed .env.example lists every variable with a fake value.
13. Evals run from the committed golden sets with one command (make eval) and write a markdown table into evals/results/. The deploy workflow (.github/workflows/deploy.yml) fails if any headline metric regresses by more than the tolerance stated in 01-backend.md.

Anti-patterns already rejected (never re-propose):
- Embedding-only retrieval for notices. Model numbers like TL1302Z do not embed well; keyword search is the baseline and probably the winner.
- A hand-rolled agent loop. Rejected for this project because the pause-for-human and crash-recovery requirements are what LangGraph is for. The trace log still makes the loop legible.
- Redis or a separate queue. Postgres status columns and a polling worker are enough at this scale.
- Supabase Auth for firm users. No organizations or roles out of the box; Clerk has both.
- pandas. Polars is the standard for this owner.
- A separate "analytics" service. The MPR generator and the completion forecast are modules in apps/api.
- Real Stripe charges or real consumer PII.

## Coding standard

- Type hints everywhere in Python; mypy strict passes. TypeScript strict; no any outside typed third-party boundaries.
- Errors handled at every changed I/O boundary: network calls, file reads, database writes, model calls. Each boundary has a timeout and a typed error.
- Structured logging (JSON lines) at every decision point: agent node entry and exit, adjudication outcome, payout state change, webhook receipt, feed pull summary. Log fields: ts, level, event, firm_id (when present), claim_id (when present), run_id (when present), duration_ms.
- No hardcoded config, model ids, prices, plan limits, or secrets. All in settings with documented defaults.
- Test floor: core logic (rule extraction parsers, range checks, duplicate detection, MPR aggregation, webhook idempotency, token signing, tenant scoping) has unit tests. Agent graphs have tests with a fake model that replays recorded tool results. Target: 85 percent line coverage on apps/api/src, measured and printed.
- Every timing, async, scheduling, or UI state change ships with a written runtime trace (production-execution skill): a short document showing the actual sequence of events observed in a run, with timestamps, not a description of what should happen.
- Ruff for lint and format with the repository config; no other formatter.
- Commit messages: imperative, one line, body explains why. No AI attribution trailers (see Attribution).

## Definition of done (per milestone)

A milestone is done only when all of the following are true and the evidence is pasted into the session summary:
1. Runs end to end from a clean clone: git clone, cp .env.example .env, docker compose up -d, make setup, make dev, and the documented demo path works.
2. Tests pass with output pasted (pytest summary and coverage line for apps/api; the vitest summary belongs to the UI owner's check of the same milestone).
3. A runtime trace exists for every user-visible flow added in the milestone, stored under docs/traces/ with the milestone number.
4. The README quickstart was executed, not read, and the commands and their outputs match the text.
5. For milestones that touch evals: evals/results/ contains the regenerated table and the diff against the previous table is explained in one paragraph.
6. The decisions log below has an entry for anything tried and abandoned during the milestone.

Milestones (one weekend each, demoable at the end of weekend 2). Each lists backend deliverables only; the UI, payments, and login owners keep their own milestone lists in their component documents and onboarding files.
- M1 (Oct 17-18): schema, migrations, database roles, RLS and the database-level cross-tenant test (dev auth provider); CPSC ingestion; regex baseline extractor; cost meter in the llm/ wrapper; rule extraction graph (fake-model tested); extraction golden set of 40 notices (model-labeled, unaudited) and eval; synthetic claim generator; rules-only adjudicator baseline; first numbers tables. Team seams: committed OpenAPI contract for every route, login seam (current_user and current_firm behind an interface, dev provider), payments seam (ledger and billing protocols with fakes, webhook stub returning 501), UI seam (make seed, make api, README curl calls), trace contract (docs/contracts/trace.md), CI for the backend half, CODEOWNERS, onboarding files.
- M2 (Oct 24-25): vision path in llm/; claim adjudication graph with tools, caps, interrupt, and checkpointer; polling worker; agent_steps tracing; duplicate and fraud clusters; API routes implemented behind the M1 contract (programs, claims, trace, portal); golden set extended to 100 owner-audited notices; document_chunks with embeddings and retrieve_policy; ablation row rules_plus_vision. Demo (with the UI owner): load a real recall, generate 500 synthetic claims, watch them adjudicate, open a trace.
- M3 (Oct 31-Nov 1): swap in the login owner's Clerk provider with no other backend change; API-level cross-tenant test with Clerk test tokens; reviewer actions with audit_log; usage_meters; entitlement hook calls at program open, adjudication, and payout creation (the payments owner implements the policy).
- M4 (Nov 7-8): payouts ledger state machine and the hook points the payments owner's Connect code calls; MPR generator with zero-discrepancy reconciliation test; full ablation table; deploy gate in CI.
- M5 (Nov 14-15, stretch): notification campaign tooling backend (owner registration matching, outreach drafts); fraud graph API.
- M6 (Nov 21-22): README executed from clean clone; backend numbers for the demo video and class slides.

Pre-committed cut order if behind at the end of M3: drop M5 entirely; then drop the MPR generator. (The completion forecast was killed on 2026-10-16; see the decisions log.) The adjudication agent, tracing, login, tenant isolation, and billing are never cut.

Pre-committed kill criteria:
- Extraction: if a regex-and-rules extractor matches the golden set within 5 points of the model-based extractor on field accuracy, the model-based extractor is demoted to a fallback and the write-up says so. The M1 golden set (40 notices, model-labeled, unaudited) gives a provisional verdict only; the 100-notice owner-audited set in M2 decides.
- Adjudication: if the rules-only adjudicator is within 0.05 F1 of the agent on the 300-claim golden set, the agent's value is in fraud detection and human routing only; the write-up leads with those numbers.
- Forecast: if the public MPR spreadsheet does not contain per-recall, per-month rows, the forecast is dropped and the MPR generator stands alone. Applied 2026-10-16: killed (decisions log).

## Working in a team

Four people, one owner per component: backend (apps/api, evals/), UI (apps/web), payments (Stripe Billing and Connect code), login (Clerk, claim tokens). The backend owner keeps the shared contracts stable so the other three can work in parallel.

- Trunk-based. main is protected in spirit: nobody commits to it directly. Work on short branches named <owner>/<topic> and open a pull request. CI (.github/workflows/ci.yml) runs ruff, mypy, and pytest with coverage for apps/api and vitest for apps/web on every pull request.
- CODEOWNERS (.github/CODEOWNERS) routes review: apps/api and evals to the backend owner; apps/web to the UI owner; apps/api/src/recallops/billing and apps/api/src/recallops/payouts/stripe_connect.py to the payments owner; apps/api/src/recallops/auth and the Clerk parts of apps/web to the login owner.
- Contracts. docs/contracts/openapi.json is the API contract the UI codes against; docs/contracts/trace.md is the agent_steps row shape and run status vocabulary the trace viewer renders. Breaking either needs a decisions-log entry and a message to the team before the pull request merges. A test fails when the generated OpenAPI document drifts from the committed one.
- Seams. Login: current_user and current_firm are FastAPI dependencies behind an AuthProvider interface; the dev provider (AUTH_PROVIDER=dev) reads X-Dev-Firm-Id and X-Dev-Role and refuses to load when any Stripe or Clerk live key is present; the login owner replaces it with the Clerk provider and nothing else in the backend changes. Payments: the payouts ledger and billing interfaces are Python protocols with fakes that allow and log; the webhook route is a 501 stub until the payments owner replaces it. UI: make seed and make api give a local backend with one real recall, one program, and 500 adjudicated synthetic claims.
- Migrations: only the backend owner adds them. Others request schema changes with a note in the decisions log.
- Model spend: nobody runs an eval that calls a model except through the llm/ meter. The M1 budget stays 2 USD total for the team.
- Onboarding: docs/onboarding/<component>.md for each non-backend owner (what exists, the seam, running the backend locally, tests that must stay green, three verification commands).

## Dead ends and settled decisions

Append-only. Date, what was tried or considered, why it died, the evidence. Re-proposing a logged dead end without new evidence is a defect.

- 2026-10-16: Product direction chosen after prior-art screen. Killed: consumer recall and warranty apps (RecallSentry, RecallDefense, CoverKeep, Sustain, Prooft, SnapRegisters), shift handover apps (NoteRelay, Shift Pilot, CosaNostra), landlord recall sentinel (too small), inspection prep (RiA-Sure, InspectRescue), predictive ordering (Supy), utility rate switching (Energy Rebate Check, Bidgely), rent compliance (LeaseBase). Survivor: recall operations for recalling firms. Closest neighbors: Tavant Recalls (automotive OEM), Tavant Warranty.AI (claims adjudication for OEMs), Smart RecallPro (Appian workflow). Delta: consumer-product segment, measured adjudication and fraud numbers, consumer payouts, MPR automation.
- 2026-10-16: CPSC recall API verified live. Base URL https://www.saferproducts.gov/RestWebServices/Recall, format=json, public, no key, documented in the 2018 programmer's guide. 31 recalls in the 30 days before Oct 16, 2026. Finding: the structured Products.Model field was blank on every sampled record; model numbers, serial ranges, and date codes live in the free-text Description. Only 4 of 31 notices mentioned serial numbers or date codes. Consequence: rule extraction from prose is the first agent task; serial-range checking is one tool among several, not the headline.
- 2026-10-16: Remedy pattern observed in the live feed: refund recalls almost always require register online, confirm model, destroy the product per instruction (cut cord, cut fabric, mark RECALLED), email a photo, receive refund by check or to original payment. Repairs route to a free kit or dealer. Consequence: the destruction-photo check is a first-class tool and eval.
- 2026-10-16: MPR schema fixed from the CPSC instructional guide (8.23.2017): total units affected (constant), corrections this period, cumulative corrections, units with manufacturer, distributor, retailers, consumers, incidents before and after recall (incidents, injuries, deaths), consumers notified by phone or text, email, mail, consumers who contacted by phone, email, mail, registration card, homepage posting, website hits, social media posts and reach, marketplace monitoring. Reports monthly, about one year. Consequence: the MPR generator has a fixed target schema.
- 2026-10-16: The public MPR spreadsheet (cpsc.gov Data page, "Recalls August 1, 2022 to Present", xlsx) could not be opened from the ideation environment. Granularity unknown. The owner will download it in a browser and attach it; verify per-recall, per-month rows before building the forecast. The data.gov catalog entry for MPR is stale (last modified 2021) and points at an HTML page; ignore it.
- 2026-10-16: Recall Bench (recallbench.com) publishes nightly CSVs rebuilt from official feeds: CPSC 2000 to present, FDA food enforcement, USDA-FSIS 2014 to present, NHTSA 24 months. Not government-affiliated. Use for the archive backfill in M1, verify a random sample of 30 rows against the official API, log the mismatch rate.
- 2026-10-16: Consumer payouts need Stripe Connect (a transfer to someone who never paid the platform is not a refund). Connect Express works in test mode at no cost but adds an onboarding step for claimants. Decision: ledger first (invariant 9), Connect in M4, export-file fallback if onboarding drags.
- 2026-10-16: FREE-HAND grant, session 1, milestone M1 only. Limits: no git push, deploy, account creation, or secret written anywhere but .env; model spend at most 2 USD for M1, and zero model calls until the meter exists; no package that is not pinned here; network only to cpsc.gov, saferproducts.gov, recallbench.com, pypi.org, registry.npmjs.org, github.com; no schema or interface change that contradicts an invariant; a test that fails after two fix attempts stops the session. Assumptions go in the chat deliverable, not in the repository.
- 2026-10-16: Scope change: team of four, one owner per component. The backend owner builds apps/api and evals/ only and ships five seams so the others work in parallel: OpenAPI contract, login seam, payments seam, UI seam (make seed, make api), trace contract. Milestones in this file list backend deliverables only.
- 2026-10-16: Session 1 red-team fixes. Owner-directed: (1) the cost meter moves into M1 between tasks 4 and 5; every model call goes through llm/ and the meter checks projected cost before the call. (2) agent_steps gets firm_id, nullable only for public-archive extraction runs; invariant 2 lists the non-tenant tables (recalls, feed_runs, stripe_events). (3) Roles recallops_owner (owns tables), recallops_app (API, NOBYPASSRLS, owns nothing), recallops_internal (jobs, BYPASSRLS); FORCE ROW LEVEL SECURITY on every tenant table; the tenancy test asserts the app role is neither superuser nor owner first. (4) docker-compose.yml and .github/workflows/ at the root; infra/ keeps render.yaml only. (5) recalls gets extracted_rules, extracted_at, extractor, extractor_version. (6) The rules-only baseline uses pure-code tools only, with destruction-photo presence only; no vision in M1; M2 adds the ablation row rules_plus_vision. (7) document_chunks moves to M2; embedding model and dimension chosen at phase 0. (8) M1 golden set is 40 notices labeled blind, extended to 100 in M2; the 15 baseline test notices and the 20 synthetic-source notices are subsets of the 40; labels are model-labeled and unaudited, and a labeling CLI is left for the owner's audit. (9) At most 3 sub-agents per task without approval. Adopted from the red-team as refinements that do not change an invariant: (10) evidence spans index one canonical source_text per recall (title, description, products, sold-at, remedy, hazard, contact, units), stored with source_text_sha256 and canonicalizer_version, because the live records carry sold-at sentences, sale dates, and prices in the structured Retailers field, so description-only spans would make verify_spans drop correct values and bias the extraction kill criterion toward the baseline. (11) NumberOfUnits parses to a value plus a status (ok, ambiguous, unparsed), never 0 for unparsed. (12) RLS policies read NULLIF(current_setting('app.firm_id', true), '')::uuid so a missing context returns zero rows instead of an error. (13) The spend ledger is agent_steps.cost_usd plus LLM_SPEND_CARRIED_USD, read under an advisory lock; there is no second logging path. (14) The M1 extraction graph is a single-pass LangGraph graph compiled without the Postgres checkpointer; invariant 3's checkpointer governs the adjudication loop from M2. (15) Golden labels are produced before either extractor exists. (16) Because the 15 baseline test notices are inside the golden set, extraction results are reported on all 40 and separately on the 25 notices the baseline was not tested against.
- 2026-10-16: Phase 0 model choice. LLM_MODEL_ID = claude-haiku-5-5, at 0.10 USD input and 0.50 USD output per million tokens for prompts up to 100K tokens (0.50 and 2.50 above). Source: the provider price table in the bundled Claude API reference (cached 2026-10-06), because api.anthropic.com is not an approved network source in session 1. Before the first live call (M2), confirm vision support through the Models API and the live price. Image tokens are estimated at one per 28x28 pixel patch (same source; verify against the vision documentation). Embeddings: the model provider has no embeddings endpoint in that reference; EMBEDDING_MODEL_ID = voyage-3.5-lite and EMBEDDING_DIM = 1024 are provisional from prior knowledge and must be confirmed on the embedding provider's documentation before the M2 migration creates document_chunks.
- 2026-10-16: MPR spreadsheet acquired; completion forecast killed. File: https://www.cpsc.gov/s3fs-public/Monthly-Progress-Report-September-Update.xlsx (sha256 a559ef28ebaa4458dd4d6482c174e4c0909ecbac3508c42f83c922763928c3fa, 129,372 bytes). Three sheets: "MPR" (38 rows, 38 distinct recalls, cumulative totals as of September 21, 2026), "MPR 08.1.22 and Post 08.1.2 (2)" (245 rows, 245 distinct recalls, as of June 20, 2023), "RecallsBetween 3.1.24 - 9.19.24" (123 rows, recall list only, no counts). No month or period column; one row per recall per snapshot. The kill criterion applies: the forecast is removed from M4, from 01-backend.md sections 8 to 11, and from the API contract. The MPR generator stands alone.
- 2026-10-16: cpsc.gov HTML pages return 403 (Akamai) to the project User-Agent. The block was not bypassed by imitating a browser. The download link was read with a page-fetch tool and the file itself was downloaded from cpsc.gov/s3fs-public with the project User-Agent (HTTP 200). Expect scripted page reads to fail; record any new link in data/raw/SOURCES.md.
- 2026-10-16: CPSC API observations from the full pull (52 windows, 1,657 records from 2022-08-01 to 2026-10-16). The Sept 15 to Oct 16 sample has 51 records (the earlier 31 counted 30 days). RecallNumber has no hyphen ("27024") while the MPR sheet uses "23-756"; joins normalize to digits. Products[].Model is blank on every sampled record; Products[].Type and CategoryID are populated and drive the golden-set stratification.
- 2026-10-16: Tried and abandoned: a 15-agent red-team workflow launched before the sub-agent cap existed. It was stopped when the cap was set; the drafts for all seven questions and the verifications for a, b, d, e, and f were kept, and c and g were verified in the main session.
- 2026-10-16: data/raw/ is gitignored except data/raw/SOURCES.md, which records URL, timestamp, size, and sha256 for every downloaded file so a clean clone can reproduce the raw data.
- 2026-10-16: Correction to the Session 1 red-team fixes, item 13: the meter reads the spend total (carried plus sum of agent_steps.cost_usd) without an advisory lock. Serializing concurrent budget checks is deferred to M2, when live calls start; M1 makes no live calls and its evals run sequentially. Evidence: llm/meter.py.
- 2026-10-16: Tried and abandoned: blind labeling by a 3-agent workflow. The first launch carried notice ids typed from memory instead of the manifest; it was stopped seconds after starting, and relaunching would have exceeded the 3-sub-agent cap, so the 40 labels were written in the main session from the notice texts and LABELING_GUIDE.md, before any extractor code existed (commit e02aa3b precedes the baseline). Labeling conventions applied to all 40: sold_at lists named retailers and websites only; registration_url only where the notice says to register, file, or fill out a form at that URL; month-only dates expand to the first and last day.
- 2026-10-16: Regex baseline 1.0.0 was developed only against the 15 baseline-test notices (owner decision that they sit inside the golden set) and frozen at 98.8 headline there; on the 25 held-out notices it scores 92.9. Two known misses are kept rather than tuned away (24149 bundled models, 26205 combined model string).
- 2026-10-16: Adjudication eval changes after the first run, both reported as rows rather than replacing the first result (evals/adjudication/PREREG.md, Revision 1): (a) tools 1.0.0 to 1.0.1, receipts matched by sha256 only, because pHash flagged 44 legitimate claims through 82 false receipt matches (template documents collide); (b) golden claims v1.0 to v1.1, three mislabeled claims fixed after a unit test showed the generator could not build an out-of-range value for two wrap-around serial notices and one one-digit-month date code. Before the first run, duplicate weights were lowered from 0.6 to 0.25 because the first draw flagged 52 percent of claims.
- 2026-10-16: Expectation recorded before M2: on the synthetic golden claims the rules-only adjudicator scores macro F1 0.982, so an agent cannot exceed it by 0.05 and the adjudication kill criterion fires on this block by construction. The agent's case rests on intact-destruction photos (vision), the 60 owner-reviewed claims, and the 40 classmate claims; the write-up leads with that.
- 2026-10-16: The extraction graph was not run live: api.anthropic.com is not among the session 1 approved network sources and no key exists, so evals/results/extraction.md reports the graph as "not run" and the extraction kill criterion as not evaluable. One recording run is estimated at no more than 0.11 USD (worst case); it needs the owner's approval of the host and a key in .env.
- 2026-10-16: evals/adjudication/outcome_policy.yaml is a DRAFT pending the owner's sign-off; the contested mapping is retailer_not_listed to needs_info rather than deny.
- 2026-10-16: Local database for M1 is postgres:16 (16.14), not pgvector/pgvector:pg16: no M1 table needs vector, the pgvector image is not on this machine, and Docker Hub is not an approved source in session 1. The M2 migration that creates document_chunks switches the image. A plain `docker compose up -d --wait` hung here with no container created; with `--pull never` it started at once, so the Makefile uses `--pull never` and the README tells a fresh machine to pull postgres:16 once.
- 2026-10-16: psycopg is used without the binary extra (psycopg-binary is not pinned). It needs a system libpq: libpq5 on Linux and CI, Homebrew libpq on macOS, whose path the Makefile passes per command because the macOS system shell strips DYLD_* variables. Adding psycopg[binary]==3.3.6 would remove the prerequisite and is an owner decision. The project is not built as a package (uv package = false) because building needs an unpinned build backend; code imports through PYTHONPATH=src.
- 2026-10-16: Schema additions beyond the section 3 list, all in migration 0001 and in 01-backend.md: recalls.source_record_id, last_publish_date, raw_sha256, source_text, source_text_sha256, canonicalizer_version, units_status, units_text, category; feed_runs.status, unchanged, failed; agent_runs.program_id, recall_id and graph value rules_only; agent_steps unique (run_id, idx) and a trigger that keeps agent_steps.firm_id equal to its run's.
- 2026-10-16: API scope pulled forward from M2 to unblock the UI owner: the read routes (recalls search, programs, claims queue and detail, trace, usage, portal view) are implemented over the seeded database; write routes return 501 with their final contract. Two routes beyond section 9 joined the contract: GET /v1/claims/{id}/photos/{photo_id} (development stand-in for signed storage URLs) and the component 3 billing stubs.
- 2026-10-16: Correction to the postgres:16 entry: in the clean-clone check (23:34 UTC) a plain `docker compose up -d` started the container immediately, so the earlier hang was most likely Docker Desktop still starting after it was launched in this session. The Makefile keeps `--pull never` (harmless with the image present) and the README sequence is unchanged.
- 2026-10-16: Repository config: ruff line length 120. Synthetic photos use Pillow's bundled default font only (no system or downloaded fonts). Whether Pillow renders byte-identical JPEGs on Linux CI is unverified until the first CI run (tests/db/test_evals_and_clis.py::test_synth_build_golden_into_tmp will show it).
- 2026-10-17: Owner approved pushing and added an API key, which authorizes api.anthropic.com for the extraction recording run (cap 0.15 USD). main was pushed to origin. The Models API confirmed claude-haiku-5-5: 1M input, 128K output, image input, structured outputs, and thinking type disabled all supported; this closes the phase 0 vision check. The live price was not re-checked (the pricing page is not an approved host); the meter keeps the reference prices.
- 2026-10-17: Extraction recording run. A one-notice smoke call (24082) showed the model naming evidence fields by path (identification.sold_from), which verify_spans did not accept, so it dropped correct dates, price, units, and remedy type. Fix in code, prompt unchanged (sha256 7dc29f49): verify_spans accepts bare or path-qualified field names. The live transport also strips schema keywords structured outputs reject (minimum, maxLength, and similar) and disables thinking. Then 40 calls, all done, 0.037476 USD (0.038389 with the smoke call); responses are committed in evals/recordings/ and `make eval` replays them at no cost and reproduces the live numbers exactly.
- 2026-10-17: Extraction kill criterion met, provisionally: model extractor headline 86.6 versus regex baseline 95.1 (minus 8.5; 86.0 versus 92.9 on the 25 held-out notices). The model extractor is demoted to a fallback until the owner-audited 100-notice set in M2 decides. Diagnostic: scored before verify_spans the model's drafts reach 92.2 (minus 2.9), so the verbatim rule costs 5.6 points (13 paraphrased destruction instructions, 11 end-of-window quotes) but the baseline still leads without it. Whether to relax verification for typed values and instructions is an M2 decision for the owner, to be logged before any rerun.
- 2026-10-17: Decisions the owner delegated: outcome_policy.yaml keeps retailer_not_listed as needs_info and is no longer a draft; psycopg[binary]==3.3.6 is added to the pins so no system libpq is needed on any machine; the clean-clone test volume and scratch copy were deleted; CODEOWNERS keeps placeholder handles (teammates clone the repository themselves); the pgvector image pull waits for M2, where it is first needed.

- 2026-10-18: Owner instruction for this session: take the backend to conclusion without asking, do the label audit, keep pushing, leave starter code for the UI, payments and login owners, and test the backend locally at the end (no deploy). Read as a FREE-HAND grant for M2 to M4 with push approved; model spend capped by the assistant at 4 USD cumulative (LLM_BUDGET_STOP_USD=4.00, warn at 3.00), under the 8 USD project stop.
- 2026-10-18: Local database image switched to pgvector/pgvector:0.8.6-pg16 (Postgres 16.15) for document_chunks; the M1 volume opened unchanged. The old image used glibc 2.41 and the new one 2.36, so Postgres reported a collation version mismatch; both databases were reindexed and their collation versions refreshed (REINDEX DATABASE, ALTER DATABASE ... REFRESH COLLATION VERSION). A fresh clone creates its volume with the new image and never sees this. CI uses the same image. The pgvector extension is created by sql/bootstrap_roles.sql as the superuser, because it is not a trusted extension.
- 2026-10-18: Extraction golden set extended to 100 notices (PREREG Revisions 1 and 2): 60 drawn with seed 20261018, labeled blind by three labeling sub-agents, and all 100 audited by the main session on the owner's delegation, with 10 corrections logged in evals/extraction/audit_log.jsonl. Status: model-labeled, assistant-audited (owner delegated), never owner-audited. Because the auditor had seen both extractors' M1 results, the 60 blind notices decide the verdict. Result on the 60, recorded live (0.055277 USD): baseline 91.8, graph 87.9 (minus 3.9). The extraction kill criterion is met and no longer provisional: the model-based extractor stays a fallback. Audit drift on the M1 40: baseline 95.1 to 94.4, graph 86.6 unchanged. Before verify_spans the graph's drafts reach 92.7 on the 60, so even a relaxed verification would leave the gap under 5 points. Verification stays strict (owner-delegated decision): every stored value must be quotable from the notice.
- 2026-10-18: The adjudication golden claims read their rules from evals/adjudication/program_rules.json, a frozen snapshot of the 20 synthetic-source gold rules from v1.1, so audits of extraction labels cannot change the 300-claim set. Regeneration reproduces all 300 claims and labels byte for byte (test_synth_build_golden_into_tmp). Two audit changes touch synthetic-source notices (24118 models, 26027 sold_at); they affect extraction scoring only.
- 2026-10-18: The red team on the M2 to M4 plan (three sub-agents: runtime, eval validity, security) found blockers that changed the design before code. Adopted: checkpointer tables hand-written in schema langgraph (the library's setup() runs CREATE INDEX CONCURRENTLY, which cannot run inside a migration transaction), with a contract test pinning the library's migration list; one LangGraph thread per run (thread_id = agent_runs.id), never per claim, because a non-resume input restarts at START and a re-run inherits stale state; a resume is claimed atomically by a lease on agent_runs, and the worker, not the graph, sets waiting_human after the interrupt is persisted; durability sync; idempotent node side effects (step_key on agent_steps, ON CONFLICT on payouts.claim_id, a fenced claim status update); caps rebuilt from counted agent_steps rows on every attach; nominal cost per step next to charged cost; replay on a virtual clock using recorded latency; deterministic prompt content (no ids, timestamps, or budgets in prompts; every list sorted). The 60-claim hard set is reported as an assistant-designed capability probe that targets rules-only blind spots by construction, never as kill-criterion evidence.
- 2026-10-18: Embeddings: the project has no embedding provider key and account creation is out of scope, so document_chunks use a deterministic local embedder, hash-ngram-v1 (word unigrams and bigrams plus character trigrams hashed into 1024 signed buckets, L2-normalized). It is lexical, not neural, and the write-up says so. A provider embedder plugs in behind the Embedder protocol. EMBEDDING_MODEL_ID defaults to hash-ngram-v1; EMBEDDING_DIM stays 1024.
- 2026-10-18: Live model calls are replay-first: a request with a recording is answered from it and charged 0 to the ledger, while run caps and eval cost columns use the nominal cost from usage. Evals record into evals/recordings (committed); the worker records into LLM_RECORD_DIR under data/generated (gitignored), so real uploads never reach the repository.

## Attribution

No AI co-author trailers or credits in commits, pull requests, or files by default. If the course requires a disclosure of AI assistance, that disclosure goes in README.md under a heading the owner approves, and this entry gets updated.
