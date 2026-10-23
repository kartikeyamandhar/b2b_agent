<p align="center">
  <img src="docs/assets/banner.svg" alt="RecallOps: the operations console for consumer product recalls. Load the recall from the CPSC feed, verify claims with an agent, catch fraud with a graph, pay refunds through a ledger, file the monthly progress report." width="100%">
</p>

<p align="center">
  <a href="https://github.com/kartikeyamandhar/b2b_agent/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/kartikeyamandhar/b2b_agent/ci.yml?branch=main&amp;label=CI" alt="CI status"></a>
  <a href="https://github.com/kartikeyamandhar/b2b_agent/actions/workflows/deploy.yml"><img src="https://img.shields.io/github/actions/workflow/status/kartikeyamandhar/b2b_agent/deploy.yml?branch=main&amp;label=eval%20gate" alt="Evaluation gate status"></a>
  <img src="https://img.shields.io/badge/coverage-91%25-1baf7a" alt="Coverage 91 percent">
  <img src="https://img.shields.io/badge/python-3.13-2a78d6" alt="Python 3.13">
  <img src="https://img.shields.io/badge/FastAPI-0.143-2a78d6" alt="FastAPI 0.143">
  <img src="https://img.shields.io/badge/LangGraph-1.2-2a78d6" alt="LangGraph 1.2">
  <img src="https://img.shields.io/badge/Postgres-pgvector-2a78d6" alt="Postgres with pgvector">
  <img src="https://img.shields.io/badge/Neo4j-2026.09-2a78d6" alt="Neo4j 2026.09">
  <img src="https://img.shields.io/badge/Claude-Haiku-2a78d6" alt="Claude Haiku">
  <img src="https://img.shields.io/badge/Next.js-16-2a78d6" alt="Next.js 16">
</p>

When a company recalls a consumer product in the United States, the press release is the easy part. The Consumer Product Safety Commission (CPSC) expects the company to reach the owners, check every refund or repair claim, deliver the remedy, and file a monthly progress report (MPR) until the recall is done. Most recalling companies are importers and mid-size brands, and they run all of this on a hotline, a spreadsheet, and a law firm.

RecallOps is the operations console for that work. It reads the recall from the public CPSC feed and turns the free-text notice into rules a program can check. Claimants file from a link on their phone, and a tool-using agent decides each claim after reading the claimant's photos, with a person deciding anything risky. Fraud rings are found across claims, refunds leave through a ledger that cannot pay twice, and the monthly report is computed from the data and reconciled to zero discrepancies.

It was built as a UCLA MSBA class project (fall 2026). Every claimant in it is synthetic, and Stripe runs in test mode, so no real money moves.

<p align="center">
  <img src="docs/assets/results-at-a-glance.svg" alt="Results at a glance: fraud recall 1.000 on the synthetic claims for rules and agent both, 0.750 on the 40 agent-written claims; agent versus rules on those 40 claims 0.774 versus 0.583 macro F1; 0 discrepancies in the monthly report; 36 of 36 duplicate groups reproduced by the Neo4j graph; 1.67 USD of model spend for the whole project." width="100%">
</p>

## Contents

1. [Try it](#try-it)
2. [Course requirements, and where they live](#course-requirements-and-where-they-live)
3. [See it](#see-it)
4. [Architecture](#architecture)
5. [Design decisions and why](#design-decisions-and-why)
6. [Repository structure](#repository-structure)
7. [Reading the code](#reading-the-code)
8. [The claims agent, step by step](#the-claims-agent-step-by-step)
9. [Fraud, money, and the monthly report](#fraud-money-and-the-monthly-report)
10. [How well it works](#how-well-it-works)
11. [Testing and quality](#testing-and-quality)
12. [Run it locally](#run-it-locally)
13. [Limitations and next steps](#limitations-and-next-steps)
14. [Project history and further reading](#project-history-and-further-reading)

## Try it

| | |
|---|---|
| Website | **https://recallops-five.vercel.app** |
| Demo company sign-in | email `recallops-demo+clerk_test@example.com`, password `Laminator-Recall-Demo-2026` |
| Verification code | `424242`, whenever Clerk asks for one |
| The demo product's claim link | [recallops-five.vercel.app/file/LL9fG4Nm...](https://recallops-five.vercel.app/file/LL9fG4NmTPWifZx8mOaaI0H4Mf-xp0fHlRzWDhhRtfoAAAABh95YxpObjeKnU4U5fn0XeZ-0StqwJx_0HaWQQGbgrVQ) (also shown on the product page) |
| Photos to file a claim with | [docs/demo-photos](docs/demo-photos): three kits, each a label, a cut-cord photo, and a receipt |

Sign-in runs on a Clerk development instance. The demo address contains `+clerk_test`, so Clerk never emails it and accepts the fixed code `424242` when it asks to verify a new device. Payments run in Stripe's test mode: no real money moves.

**1. Look around as the company.** Sign in with the demo login and open the product, the Scotch thermal laminator recall (CPSC 27024). Six synthetic claims were filed through its public link on 2026-10-23 and decided by the live agent:

| Claimant | Status | What it shows |
|---|---|---|
| Avery Demo | Paid | The agent asked for a clearer photo of the cut cord. The claimant replied with one, the agent approved, the claimant chose a mailed check, and the check batch was exported |
| Emery Demo | Approved | The refund waits for the claimant to choose a check or a bank transfer |
| Casey Demo | Denied | The label photo shows model TL9000Q; the recall covers only TL1302Z |
| Drew Demo (two claims) | Flagged | Both reuse Avery's label photo, so they wait for a reviewer |
| Blake Demo | Flagged | Its cut-cord photo is a near copy of three other claims' photos (perceptual hashes 6 bits apart or closer) |

Open a claim for its photos, decision, and agent trace. Approve or deny a flagged claim as the reviewer, then look at **Fraud rings**, **Payouts**, and **Monthly reports**. Anything you change is what the next visitor sees.

**2. File a claim as a claimant.** Open the claim link on a phone or in a narrow window and start a claim. Upload the three photos of one kit from [docs/demo-photos](docs/demo-photos), enter model `TL1302Z`, retailer `Amazon.com`, and the kit's purchase date (kit 1: 2025-02-08, kit 2: 2025-09-30, kit 3: 2026-01-12), and make up a name, an `example.com` email, and a street address. The agent decides the claim within a minute (13.8 seconds in the first live check), and the claim's private link shows the result. Each kit can be approved once: a later claim with the same photos, email, or address is flagged as a likely duplicate, which is the fraud check at work. The destruction check is strict with drawings, so it may ask for another photo; answer it from the claim's page.

**3. Sign up your own company.** Sign up with any address that contains `+clerk_test` (for example `yourname+clerk_test@example.com`, code `424242`), name the company, and pick Free. Under **Recall access**, ask for any recall except the laminator, which the demo company holds (one company per recall). The request then waits for the platform operator, who approves each company for each recall so nobody can run a recall in another company's name. The operator account belongs to the project owner and is not shared, so this path ends at "Waiting for review" unless the owner approves it.

Billing: the test card 4242 4242 4242 4242 upgrades a company to Growth.

## Course requirements, and where they live

| Requirement | How RecallOps meets it | Where to look |
|---|---|---|
| ReAct-style agent loop | The claims agent alternates reasoning and acting. The model reads the claim, calls typed tools (read the label photo, check the model, check the purchase, look for duplicates, look up policy text), reads the results, and repeats until it calls `decide` with a citation on every reason. Each step is a row in `agent_steps`, which the console's trace viewer shows. | [claims/graph.py](apps/api/src/recallops/claims/graph.py#L577), [claims/agent_tools.py](apps/api/src/recallops/claims/agent_tools.py#L224) |
| Retrieval | The `retrieve_policy` tool searches the recall's own documents. Notice text is ranked by Postgres full-text search; prose (FAQ, remedy terms) by full-text search and pgvector, fused by reciprocal rank. | [retrieval/search.py](apps/api/src/recallops/retrieval/search.py#L107) |
| A modern agent framework | LangGraph 1.2 with its Postgres checkpointer. A claim that needs a person stops on a LangGraph interrupt, and the reviewer's decision resumes the same run. | [claims/graph.py](apps/api/src/recallops/claims/graph.py#L559), [claims/worker.py](apps/api/src/recallops/claims/worker.py#L106) |
| Login | Clerk. One account is one company; the API verifies each session token against Clerk's public keys and sets the company for row-level security. Claimants never get accounts: they use signed links. | [auth/clerk.py](apps/api/src/recallops/auth/clerk.py#L57), [auth/hmac_tokens.py](apps/api/src/recallops/auth/hmac_tokens.py#L39), [apps/web/src/lib/auth](apps/web/src/lib/auth) |
| Payments | Stripe in test mode. Checkout and the Customer Portal for company plans, usage metering, signed webhooks, and Connect transfers for claimant refunds behind a ledger. | [billing/service.py](apps/api/src/recallops/billing/service.py#L58), [billing/webhooks.py](apps/api/src/recallops/billing/webhooks.py#L36), [payouts/](apps/api/src/recallops/payouts) |

## See it

A claim the agent denied, from a run recorded live: the agent read the label photo as model TL9000Q, checked it against the recall's only model, TL1302Z, and tied every reason to a rule or a check it ran.

<p align="center">
  <img src="docs/assets/img/ui-claim-deny.png" alt="Claim detail in the RecallOps console: the claimant's details; the label photo with the reading model TL9000Q, serial 00007002, confidence 97 percent, linked to step 2; the destruction photo with the reading that the cord appears cut; and the agent's decision, Deny, with five reasons citing rules R:models, R:sold_at, R:sold_window, R:receipt, R:destruction and tool results T1 to T5." width="100%">
</p>

<table>
<tr>
<td width="50%"><img src="docs/assets/img/ui-trace-deny.png" alt="The agent trace for the same claim: 5 of 8 counted steps, 0.0017 of 0.02 USD, 9.094 of 60 seconds; each step shows its node, tool, latency, tokens, cost, and whether the decision cites it." width="100%"></td>
<td width="50%"><img src="docs/assets/img/ui-queue.png" alt="The claims queue for the laminator recall: 117 claims need a reviewer; filters by status with counts (flagged 117, needs info 107, approved 128, denied 94, paid 54); each row shows status, outcome, model, retailer, refund, and dates." width="100%"></td>
</tr>
<tr>
<td valign="top"><sub><b>The trace.</b> Every step the agent took, with its cost and time against the run's caps, and which steps the decision cites.</sub></td>
<td valign="top"><sub><b>The queue.</b> The 500 seeded claims. The 117 that need a person are counted at the top and marked in the list.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/assets/img/ui-fraud.png" alt="Fraud rings: 58 rings covering 117 claims, filtered by what the claims share (same photo 14, same home address 13, same serial number 17, same payout destination 14), each ring drawn as a small diagram with its claims and statuses." width="100%"></td>
<td width="50%"><img src="docs/assets/img/ui-overview.png" alt="The product page: 53,634 units affected, 500 claims filed, 117 awaiting review, 107 waiting on the claimant, 54 corrected, review above 100 dollars; the product's public claim link with its QR code, poster, and replace buttons." width="100%"></td>
</tr>
<tr>
<td valign="top"><sub><b>Fraud rings.</b> Claims that share a photo, an address, a serial number, or a payout account, read from the Neo4j graph.</sub></td>
<td valign="top"><sub><b>The product.</b> One recall's working page: units, claims by status, corrections, and the one public claim link with its QR code.</sub></td>
</tr>
</table>

<table>
<tr>
<td width="24%"><img src="docs/assets/img/ui-portal-form.png" alt="The claim link on a phone: the company running the recall, the recall title and number, which model it covers, where it was sold, what the claimant gets, the three photos the claim needs, and a Start my claim button." width="100%"></td>
<td width="24%"><img src="docs/assets/img/ui-portal-status.png" alt="The claimant's private claim page on a phone: your claim was approved; choose a mailed check or a bank transfer set up on Stripe's page, with the date after which a check is mailed by default." width="100%"></td>
<td valign="middle"><b>The claimant's side.</b> A company shares one link and QR code per product. Starting a claim from it gives the claimant a private link to that one claim, with no account and no password. The claimant takes three photos, says where the product was bought, and submits. The same private link then shows the decision in plain words, asks any follow-up question, and offers a mailed check or a Stripe bank transfer once a refund is approved. A claim waiting for a person reads as "under review": claimants never see internal words like flagged.<br><br><sub>Screenshots of the website in <code>apps/web</code>, running locally on seeded synthetic data. It follows the system's dark mode too.</sub></td>
</tr>
</table>

## Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" alt="Architecture in four layers. People: company staff, claimants, and the platform operator. The website on Vercel: console, claim portal, operator window; its server makes every API call. The backend on Render: the FastAPI API, the worker that runs the LangGraph claims agent under caps of 8 steps, 0.02 USD, and 60 seconds, and two scheduled jobs for refunds and the check export. Data: Postgres with pgvector and row-level security, a private photo bucket, and a Neo4j fraud graph that decides nothing. Outside services over HTTPS: Clerk, the CPSC recall API, Claude Haiku, and Stripe in test mode. The monthly progress report PDF is the output a company files with CPSC." width="100%">
</p>

One FastAPI service and one worker sit over Postgres. The website never calls the API from the browser: its server makes every call, carrying the company's Clerk session token or the claimant's signed link. Everything stateful lives in Postgres, including the agent's checkpoints; the photo bucket holds bytes and the graph holds a copy built for searching.

How one claim moves through the system:

1. **Load the recall.** A nightly GitHub Actions job asks the API to pull the CPSC feed (on 2026-10-23 it added 5 new recalls). When the operator approves a company for a recall, the API turns the notice into identification rules (model numbers, serial and date-code ranges, retailers, the sale window) and remedy rules (refund, repair, or replacement, and what the claimant must show). The regex extractor runs first. The model extractor is a fallback, and any value it cannot quote word for word from the notice is dropped.
2. **Take the claim.** The product's public link starts a claim and hands the claimant a private link to it, signed with HMAC-SHA256 and valid for 90 days. Starts are rate-limited per network address and per link.
3. **Decide it.** The worker leases the next submitted claim and runs the agent inside hard caps on steps, cost, and time. The agent may be stricter than the rules table on its own. A flagged claim, an approval over the product's money threshold, a run that hits a cap, and any decision more lenient than the rules wait for a person.
4. **Catch fraud.** Reused photos, addresses, serial numbers, and payout destinations are caught in Postgres when each claim is decided. Neo4j then links them into rings for a reviewer.
5. **Pay.** An approval and its payout row commit in one transaction. The claimant picks a mailed check or a bank transfer, and money leaves only through the ledger, once.
6. **Report.** The monthly progress report is computed in SQL, checked against an independent recount, and rendered to PDF.

| Part | Runs on | Plan |
|---|---|---|
| Website (`apps/web`) | Vercel | Hobby (free) |
| API (`recallops-api`) | Render web service | Starter, about $7 a month |
| Worker and two cron jobs | Render | Starter, about $7 a month for the worker; the cron jobs cost cents |
| Postgres and photo storage | Supabase | Free |
| Fraud graph | Neo4j AuraDB | Free |
| Sign-in | Clerk development instance | Free |
| Payments | Stripe sandbox, test mode | Free |

The API moved from Render's free plan to Starter on 2026-10-23, so it no longer sleeps between visits (the free plan took 42 seconds to wake that morning). [docs/DEPLOY.md](docs/DEPLOY.md) lists every service, setting, and secret, and how to deploy again.

## Design decisions and why

Each decision below is recorded, dated and with its evidence, in the decisions log at the end of [CLAUDE.md](CLAUDE.md#dead-ends-and-settled-decisions).

| Decision | Why | Rejected alternative | Evidence |
|---|---|---|---|
| The agent loop is LangGraph with the Postgres checkpointer | Claims must pause for a person and survive a crash. A LangGraph interrupt saves the run; the reviewer's action resumes the same run, and a crashed run is picked up where it stopped. | A hand-written loop, which would need its own pause, resume, and recovery code | [claims/graph.py](apps/api/src/recallops/claims/graph.py#L577), [docs/traces/M2-adjudication.md](docs/traces/M2-adjudication.md) |
| Rules decide where rules can see; the agent may only be stricter on its own | On claims generated from the rules, plain rules score 0.982 macro F1 and the agent 0.916. The agent earns its place where rules are blind (photos, missing labels). A decision more lenient than the rules waits for a person. | Letting the agent overrule the rules table in either direction | [evals/results/ablation.md](evals/results/ablation.md), [PREREG.md](evals/adjudication/PREREG.md) |
| Regex extraction first, the model as a fallback, and every stored value quotable from the notice | On 60 notices labeled blind, regex scored 91.8 and the model 87.9. A rule that cannot be quoted from the notice could approve or deny claims on text the recall never said. | The model-based extractor as the default | [evals/results/extraction.md](evals/results/extraction.md), [rules/verify.py](apps/api/src/recallops/rules/verify.py#L33) |
| Keyword search for notices; vectors only for prose | Model numbers like TL1302Z are tokens, not meanings, and embed poorly. Keyword-only retrieval tied full retrieval (0.774 on the 40-claim block). The embedder is a local hashed n-gram model, so no second AI provider is needed. | Embedding-only retrieval | [retrieval/search.py](apps/api/src/recallops/retrieval/search.py), the ablation's retrieval rows |
| Postgres row-level security, forced, under an API role that owns nothing and bypasses nothing | Company data must never cross companies, even if a query forgets its filter. The company id comes from the verified token and is set per transaction; repositories filter too, so there are two guards. | Application-level filtering only | [db/session.py](apps/api/src/recallops/db/session.py#L17), [tests/db/test_tenancy.py](apps/api/tests/db/test_tenancy.py#L115) |
| Payouts are a ledger first | A payout row exists before any Stripe call and its id is the Stripe idempotency key, so a retry or a crash cannot pay twice. An unknown Stripe outcome stays open for reconciliation and is never turned into a check. | Calling Stripe from the approval and recording afterwards | [payouts/db_ledger.py](apps/api/src/recallops/payouts/db_ledger.py#L196), [payout-states.svg](docs/assets/payout-states.svg) |
| Neo4j is a projection and stays out of the money path | AuraDB Free pauses when idle. Duplicate checks that hold payouts run in Postgres, so a graph outage never stops or releases money. Graph tenancy is structural: every node and relationship carries the company id, and two companies with the same photo never share a node. | Deciding fraud holds from the graph | [fraud/sync.py](apps/api/src/recallops/fraud/sync.py#L302), [CLAUDE.md](CLAUDE.md) invariant 2 |
| A polling worker on Postgres status columns | At this scale a status column, a lease, and `FOR UPDATE SKIP LOCKED` do the job of a queue, with one less service to run and keep consistent. | Redis or another queue | [claims/worker.py](apps/api/src/recallops/claims/worker.py#L121) |
| Every model call goes through one metered wrapper, and every evaluation call is recorded | The project budget is under 20 USD. The meter refuses a call before it would cross a run cap or the budget. 3,493 recorded responses let every evaluation rerun offline at no cost, so CI can gate on the numbers. | Calling the provider SDK directly from each feature | [llm/meter.py](apps/api/src/recallops/llm/meter.py#L113), [evals/recordings](evals/recordings) |
| One account is one company, and the operator approves each company for each recall | In the first live test, any signed-up account could open any recall and pay refunds in that company's name. Approval by a platform operator closes that. | Self-service recall access, or matching email domains | [access.py](apps/api/src/recallops/access.py), decisions log 2026-10-22 |
| One public claim link per product, a private link per claim, no claimant accounts | A claimant files once. A link and a QR code on a poster are easier than an account, and a signed private link keeps each claim separate. | Claimant accounts with passwords | [auth/intake_tokens.py](apps/api/src/recallops/auth/intake_tokens.py#L46), [auth/hmac_tokens.py](apps/api/src/recallops/auth/hmac_tokens.py#L39) |
| Expectations are written down before each evaluation run | So a result cannot be tuned into a success after the fact. Misses are reported as misses. | Reporting only the final numbers | [evals/adjudication/PREREG.md](evals/adjudication/PREREG.md) |

## Repository structure

```
.
├── apps/
│   ├── api/                      the backend, Python 3.13
│   │   ├── src/recallops/        one package per concern (table below)
│   │   ├── migrations/versions/  9 Alembic migrations: schema, roles, row-level security, triggers
│   │   ├── sql/                  database role bootstrap
│   │   └── tests/                unit/ (no database), db/ (real Postgres and Neo4j), api/ (HTTP end to end)
│   └── web/                      the website, Next.js 16 and TypeScript
│       ├── src/app/              routes: /app (console), /file and /claim (claim portal), /operator
│       ├── src/components/       React components
│       ├── src/lib/              the server-side API client, the sign-in seam, page logic
│       └── tests/                vitest
├── evals/                        golden sets, pre-registrations, 3,493 recorded model calls, results tables
├── docs/
│   ├── assets/                   the diagrams and screenshots on this page
│   ├── components/               the design of each component: backend, website, payments, login
│   ├── contracts/                the OpenAPI contract, the trace contract, and their change log
│   ├── traces/                   timelines recorded from real runs, one per flow
│   └── DEPLOY.md                 how the live site is deployed
├── data/samples/                 the committed CPSC fixture: 51 real recall records
├── infra/render.yaml             the Render blueprint: API, worker, two cron jobs
├── .github/workflows/            CI, the evaluation gate, the nightly CPSC pull
├── docker-compose.yml            local Postgres with pgvector and Neo4j, bound to 127.0.0.1
├── Makefile                      every command: make help lists them
└── CLAUDE.md                     the project's rules, invariants, and dated decisions log
```

The backend package, `apps/api/src/recallops/`:

| Package | What it does | Start reading at |
|---|---|---|
| `api/` | FastAPI app, routes, request guards and rate limits, RFC 7807 errors | [routes.py](apps/api/src/recallops/api/routes.py) |
| `auth/` | Clerk session tokens, signed claim and product links, the development sign-in | [provider.py](apps/api/src/recallops/auth/provider.py#L38) |
| `db/`, `tenancy.py` | Engines, per-transaction company context, company-scoped repositories | [session.py](apps/api/src/recallops/db/session.py#L17) |
| `ingest/` | The CPSC feed pull and unit-count parsing | [runner.py](apps/api/src/recallops/ingest/runner.py) |
| `rules/` | Notice to rules: the regex baseline, the model fallback, span verification | [baseline.py](apps/api/src/recallops/rules/baseline.py#L342) |
| `claims/` | The agent graph, its tools, vision checks, the rules-only checker, the worker, decision effects | [graph.py](apps/api/src/recallops/claims/graph.py#L231) |
| `llm/` | The model client, prices, and the cost meter | [meter.py](apps/api/src/recallops/llm/meter.py#L95) |
| `retrieval/` | Document chunks, the local embedder, hybrid search | [search.py](apps/api/src/recallops/retrieval/search.py#L71) |
| `fraud/` | Photo hashing and the Neo4j projection of duplicate links | [sync.py](apps/api/src/recallops/fraud/sync.py#L302) |
| `payouts/` | The payout ledger, Stripe Connect transfers, the check export | [db_ledger.py](apps/api/src/recallops/payouts/db_ledger.py#L196) |
| `billing/` | Company plans, Checkout, the Customer Portal, usage, webhooks | [service.py](apps/api/src/recallops/billing/service.py#L58) |
| `mpr/` | The monthly progress report, its independent recount, the PDF | [generate.py](apps/api/src/recallops/mpr/generate.py#L127) |
| `portal.py`, `intake.py`, `programs.py`, `access.py`, `campaigns.py` | The claimant portal, claim starts, products, recall access, owner notices | |
| `synth/`, `evals/`, `seed.py` | Synthetic claims and photos, evaluation runners and the gate, demo data | [evals/run_all.py](apps/api/src/recallops/evals/run_all.py) |

## Reading the code

To follow one claim through the code, read these in order:

1. [portal.py](apps/api/src/recallops/portal.py) and [intake.py](apps/api/src/recallops/intake.py): a claimant starts and submits a claim through the product link.
2. [claims/worker.py](apps/api/src/recallops/claims/worker.py#L121): the worker leases the claim with `FOR UPDATE SKIP LOCKED` and attaches a LangGraph run to it.
3. [claims/graph.py](apps/api/src/recallops/claims/graph.py#L577): the graph. `load_context` loads rules and photos, `agent` and `tools` loop, `route` applies the outcome, and `human_review` is the interrupt.
4. [claims/agent_tools.py](apps/api/src/recallops/claims/agent_tools.py#L224): each tool's arguments are a pydantic model, so a bad call is refused before it runs, and [validate_decision](apps/api/src/recallops/claims/agent_tools.py#L298) refuses a reason without a citation.
5. [llm/meter.py](apps/api/src/recallops/llm/meter.py#L113): the preflight check every model call passes.
6. [claims/effects.py](apps/api/src/recallops/claims/effects.py#L213): the decision, its payout row, and any duplicate reopenings commit in one transaction.
7. [payouts/db_ledger.py](apps/api/src/recallops/payouts/db_ledger.py#L137) and [payouts/stripe_connect.py](apps/api/src/recallops/payouts/stripe_connect.py#L239): the payout's legal state changes and the Stripe transfer.
8. [mpr/generate.py](apps/api/src/recallops/mpr/generate.py#L127) and [mpr/recount.py](apps/api/src/recallops/mpr/recount.py#L76): the report and the recount that must agree with it.

Conventions that hold everywhere: type hints with mypy strict and TypeScript strict; ruff for lint and format; settings only from the environment, with every variable listed in [.env.example](.env.example) (a test checks it); structured JSON logs at each decision point; a timeout and a typed error at every network, file, database, and model boundary; and tests grouped by what they need: `tests/unit` runs without a database, `tests/db` against real Postgres and Neo4j, and `tests/api` through HTTP.

## The claims agent, step by step

<p align="center">
  <img src="docs/assets/agent-loop.svg" alt="The claims agent. load_context leads to the agent node, which loops with the tools node: vision tools read_label_photo and check_destruction_photo; pure-code tools check_model, check_serial, check_date_code, check_purchase, check_duplicates; and retrieve_policy. The agent ends by calling decide, whose reasons must cite rules or tool results. route then applies approve, deny, or needs_info, or holds the claim for human_review, which resumes the same run. Run caps: 8 steps, 0.02 USD, 60 seconds." width="100%">
</p>

The agent picks which checks to run, reads the photos with a vision model, and has to justify its decision: every reason cites a rule from the notice (`R:models`) or the result of a tool it ran (`T5`). A reason without a citation is refused and the model gets one retry. Here is the run behind the screenshots above, recorded live, for a claim whose label shows a model the recall does not cover:

| step | node | what happened | latency | cost (USD) |
|---|---|---|---|---|
| 0 | `load_context` | rules R:policy, R:models, R:sold_at, R:sold_window, R:receipt, R:destruction, R:value_threshold; three photos | 0 ms | 0 |
| 1 | `agent` | asks for four independent checks in one turn | 1,706 ms | 0.000366 |
| 2 | `read_label_photo` | **T1**: legible, model `TL9000Q`, confidence 0.97 | 1,640 ms | 0.000163 |
| 3 | `check_purchase` | **T2**: Amazon.com is a listed retailer, inside the sale window, receipt present | 0 ms | 0 |
| 4 | `check_duplicates` | **T3**: no shared photo, address, serial, or payout account | 2 ms | 0 |
| 5 | `check_destruction_photo` | **T4**: complies; "a black cord that appears cut or severed" | 1,229 ms | 0.000164 |
| 6 | `agent` | TL9000Q is not on the recalled list, so it confirms with `check_model` | 1,865 ms | 0.000393 |
| 7 | `check_model` | **T5**: mismatch against the recalled model `TL1302Z` | 0 ms | 0 |
| 8 | `agent` | calls `decide` | 2,652 ms | 0.000621 |
| 9 | `decide` | accepted: every reason is cited | 0 ms | 0 |
| 10 | `route` | **deny**, matches the rules table, no hold | 0 ms | 0 |

```text
outcome: deny        confidence: 0.93        total: 0.0017 USD, about 9 seconds

[R:models, T1, T5]   The label photo is legible and shows model TL9000Q. The program's only recalled
                     model is TL1302Z, and the model check reports a mismatch, so the product is not
                     covered by the recall.
[R:sold_at, R:sold_window, R:receipt, T2]
                     The purchase is from Amazon.com, inside the sale window, and a receipt is present.
                     These checks passed but do not cure the model mismatch.
[T3]                 No other claims share a photo, address, serial, or payout account with this claim.
[R:destruction, T4]  The destruction photo appears to show a cut power cord, which meets the destruction
                     requirement. This does not change the outcome because the model is not recalled.
```

<sub>The decision had five reasons; the one about the typed model matching the label is left out above for space. The run is claim C2 of the <a href="docs/traces/M2-adjudication.md">adjudication trace</a>.</sub>

What a claimant uploads, and what the checks make of it:

<table>
<tr>
<td width="33%"><img src="docs/assets/img/sample-label.jpg" alt="Synthetic product label reading MODEL 98527, SER SN31887997, MADE IN CHINA, with a barcode"></td>
<td width="33%"><img src="docs/assets/img/sample-destruction.jpg" alt="Synthetic photo of a gray device whose black power cord has a visible gap where it was cut"></td>
<td width="33%"><img src="docs/assets/img/sample-receipt.jpg" alt="Synthetic test receipt: store ALDI, a date, the recalled product, a total, stamped SYNTHETIC"></td>
</tr>
<tr>
<td valign="top"><sub><b>Label.</b> Vision reads the model as 98527 (confidence 0.9) and checks it against the notice's model list.</sub></td>
<td valign="top"><sub><b>Destruction.</b> The notice says to cut the power cord. Vision: "the cord appears severed with a visible gap between the two cord segments."</sub></td>
<td valign="top"><sub><b>Receipt.</b> No model reads it. The retailer and date the claimant typed are checked against the notice, and the receipt's hash catches a receipt filed twice.</sub></td>
</tr>
</table>

## Fraud, money, and the monthly report

### Fraud rings

<p align="center">
  <img src="docs/assets/fraud-clusters.svg" alt="Fraud clusters in the 500 seeded synthetic claims: same photo 14, same address 13, same serial 17, same payout destination 14; 58 clusters covering 117 claims." width="100%">
</p>

Fraud in a recall shows up as reuse: one label photo filed twice, one address behind several claims, a serial number already claimed, one payout account collecting many refunds. The duplicate check runs in Postgres on every claim. If a duplicate arrives after an earlier claim was approved but before its money moved, the earlier claim is reopened and its payout held in the same transaction. Each decided claim is then copied into Neo4j, where connected claims form the rings a reviewer sees. On the 300 golden claims the graph reproduces all 36 duplicate groups exactly. Identity keys in the graph are keyed hashes, never a raw address or email.

### Money that moves once

<p align="center">
  <img src="docs/assets/payout-states.svg" alt="Payout ledger state machine. pending, onboarding, and failed are payable. Each can be claimed for a transfer (transferring), sent to export (pending_export), or held. transferring ends in transferred or returns to failed; pending_export ends in exported or is held; a re-approved held row returns to pending, or to pending_export when it was a check. transferred and exported are terminal and mark the claim paid. Only a person's re-approval releases a held row." width="100%">
</p>

A transfer is claimed under a row lock, and a held payout can never be claimed. A payout waiting for a mailed check (the claimant chose one, three transfers failed, or nobody chose within 14 days) joins the next export batch, and the batch file is written in the same transaction that marks its payouts exported, so two export runs can never pay the same claim. A check sent to an address another claim already uses goes to a person first.

### The monthly progress report, reconciled three ways

The report is computed in SQL. An independent recount over plain row exports, written in Polars with its own definitions, must agree on every computed box, and the test scenario keeps its own ground truth as it writes the rows. The scenario is built to trip careless definitions: a replayed delivery event, deliveries on the boundary instant between two months, claims answered the month after they were filed, and a second program whose numbers must stay out.

| month | corrections (report / recount / truth) | cumulative | email contacts | discrepancies |
|---|---|---|---|---|
| 2026-08 | 19 / 19 / 19 | 19 / 19 / 19 | 161 / 161 / 161 | **0** |
| 2026-09 | 87 / 87 / 87 | 106 / 106 / 106 | 188 / 188 / 188 | **0** |
| 2026-10 | 68 / 68 / 68 | 174 / 174 / 174 | 153 / 153 / 153 | **0** |

Mutation tests plant the classic bugs (a month boundary off by one instant, counting rows instead of distinct deliveries), and the reconciliation fails on each. Full table: [evals/results/reporting.md](evals/results/reporting.md).

## How well it works

<p align="center">
  <img src="docs/assets/ablation.svg" alt="Adjudication ablation, macro F1 for six configurations on three blocks. Synthetic golden claims: rules only 0.982, agent loop 0.916. Rules-blind probe: rules only 0.095, agent loop 0.703. Written blind to the generator: rules only 0.583, agent loop 0.774." width="100%">
</p>

Six ways of deciding the same claims under the same outcome policy, on three blocks of claims. Every model call is recorded, so the table replays offline at no cost, and the evaluation gate regenerates it on every pull request.

| adjudicator | synthetic golden (300) | rules-blind probe (60) | written blind to the generator (40) | model cost per claim |
|---|---|---|---|---|
| Rules only | **0.982** | 0.095 | 0.583 | 0 |
| Rules + vision | 0.933 | **0.726** | 0.668 | 0.0002 USD |
| Single pass | 0.919 | 0.663 | 0.710 | 0.0009 USD |
| **Agent loop** | 0.916 | 0.703 | **0.774** | 0.0016 USD |
| Agent, no retrieval | 0.908 | 0.699 | 0.718 | 0.0015 USD |
| Agent, keyword retrieval only | 0.915 | 0.691 | **0.774** | 0.0016 USD |

<sub>Macro F1 is the unweighted mean of the per-outcome F1 scores (approve, deny, needs_info, flag), so a rare outcome counts as much as a common one. The golden claims are generated from the same rules the rules-only checker applies, so it sits near its ceiling there. The probe is assistant-designed around rules' blind spots; the third block was written by a separate agent without access to the generator, the tools, or the labels. Source: <a href="evals/results/ablation.md">evals/results/ablation.md</a>.</sub>

What the numbers say, including where the agent loses (every expectation was written down first, in [PREREG.md](evals/adjudication/PREREG.md)):

- On claims generated from the rules, plain rules win, 0.982 against 0.916. This fired the kill criterion set before the run: the agent has to earn its place elsewhere.
- Where rules are blind, reading the photos is not. On the 60-claim probe (a label that contradicts the typed model, a "destroyed" product with its cord intact, an unreadable label), rules score 0.095 by design, the agent 0.703, and rules plus the two vision checks 0.726.
- The loop does best on the block written blind to the generator: 0.774 against 0.583 for rules and 0.710 for a single pass. Its edge over the single pass is 3 claims of 40.
- Two expectations missed and are reported as misses. Retrieval helped by 0.056 on the third block, where it was expected to make no difference. And the vision check rejects many of the generator's compliant destruction photos, which keeps the photo rows under their 0.75 target on the probe.

On real photos. To separate the vision tools from synthetic drawings, 64 photos that CPSC published with its recall notices were drawn by a seeded rule, labeled by two blind labelers who agreed on all 64, and run once (0.0094 USD; replayed since). Details: [evals/cpsc_photos/PREREG.md](evals/cpsc_photos/PREREG.md).

| what the tool should do | real CPSC photos | result |
|---|---|---|
| Read the model number | 20 readable labels | 18 read, 1 misread, 1 abstained |
| Never make one up | 20 with no readable model number | none invented; 2 returned another printed code |
| Reject an intact product | 20 product photos | 20 rejected |
| Accept CPSC's own examples of correct destruction | 3 | **1 accepted** |

Rule extraction: on the 60 notices labeled blind, the regex baseline beats the model-based extractor on field accuracy, 91.8 against 87.9 ([evals/results/extraction.md](evals/results/extraction.md)), so the model runs only as a fallback, as the pre-registered kill criterion requires.

## Testing and quality

| | |
|---|---|
| Backend tests | 616 pass (pytest), 91 percent line coverage; ruff and mypy strict are clean (`make check`) |
| Website tests | 799 pass (vitest), plus the TypeScript strict check and a check that the generated API types match the contract (`make web-check`) |
| CI | Every pull request runs the backend suite against real Postgres and Neo4j service containers, and the website's checks and build |
| Evaluation gate | `make eval` replays all 3,493 recorded model calls offline. The gate fails a change if any headline metric regresses past its tolerance, or if the committed results tables differ from what replay produces |
| Tenant isolation tests | The API's database role is checked to be neither superuser nor table owner, and one company's context provably cannot read, change, or delete another's rows ([test_tenancy.py](apps/api/tests/db/test_tenancy.py)) |
| Runtime traces | Each user-facing flow has a timeline recorded from a real run, in [docs/traces](docs/traces) |
| Synthetic data only | Claimants, photos, and owner lists are generated. Emails use example domains, phone numbers the reserved 555-01xx block, and the owner-list upload refuses anything else |
| Model spend | 1.67 USD for the whole project, metered per call |

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/) 0.11.21, Python 3.13, Node 22, and make. A machine without the images pulls them once: `docker pull pgvector/pgvector:0.8.6-pg16 && docker pull neo4j:2026.09.0-community`.

```bash
git clone https://github.com/kartikeyamandhar/b2b_agent.git recallops && cd recallops
cp .env.example .env
docker compose up -d      # Postgres 16 + pgvector and Neo4j, bound to 127.0.0.1
make setup                # pinned dependencies for the API and the website, database roles, migrations
make seed                 # a real recall, 500 synthetic claims decided, payouts, fraud rings (about a minute)
make dev                  # the API on http://127.0.0.1:8000, interactive docs at /docs
make web                  # in a second terminal: the console at http://localhost:3000/app/programs
```

`make seed` loads a fixture of 51 real CPSC records and opens a product for the Scotch thermal laminator recall (CPSC 27024) under a demo company. It then generates 500 claims, decides them with the rules-only checker (no model calls), moves the approved refunds through the ledger, and builds the fraud graph. The ids are deterministic.

Locally the console signs you in as the demo company's admin (set `RECALLOPS_DEV_ROLE=reviewer` in `apps/web/.env.local` for the reviewer's view). Open a claim to see its photos, decision, and trace. The product page shows the claim link: open it in a phone-sized window to file a claim as an owner. The backend also serves a one-page test console at http://127.0.0.1:8000/dev/console that calls every route; it is never served in production.

To watch new claims get decided, in a third terminal:

```bash
P=0874f66f-bfb5-552b-bd93-6266f49c1d5a
PYTHONPATH=apps/api/src uv run --project apps/api --frozen python -m recallops.synth --program $P --n 20 --seed 7
ADJUDICATOR=rules_only make worker
```

`ADJUDICATOR=rules_only` runs offline at no cost. The agent itself (`ADJUDICATOR=agent LLM_MODE=live`, about 0.0016 USD per claim) needs an Anthropic key in `.env`, and every call goes through the cost meter.

Other targets: `make check` (lint, strict types, backend tests), `make web-check` (website types and tests), `make eval` (every evaluation from the recordings, no network), `make graph-sync` (rebuild the fraud graph from Postgres), and `make help` for the rest.

<details>
<summary><b>API calls you can paste against a seeded local API</b></summary>

In development, requests authenticate with headers (`AUTH_PROVIDER=dev`). Errors are RFC 7807 problem details with a `correlation_id`.

```bash
B=http://127.0.0.1:8000
F=00000000-0000-4000-8000-00000000d001      # demo company
G=00000000-0000-4000-8000-00000000d002      # another company (5 claims)
P=0874f66f-bfb5-552b-bd93-6266f49c1d5a      # demo product
C=66dcc492-086f-5b59-b441-cbe001eb2e0b      # a demo claim
T=dev.$F.$C                                 # its private claim link token (dev tokens are dev.<company>.<claim>)
A=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: admin")
R=(-H "X-Dev-Firm-Id: $F" -H "X-Dev-Role: reviewer")

# Health (readyz needs a worker heartbeat and reports the graph)
curl -s $B/healthz
curl -s $B/readyz

# Recall archive search (full text)
curl -s "${R[@]}" "$B/v1/recalls?q=laminator&limit=5"

# Products (called programs in the API)
curl -s "${R[@]}" $B/v1/programs
curl -s "${R[@]}" $B/v1/programs/$P

# Claims queue, detail, trace
curl -s "${R[@]}" "$B/v1/programs/$P/claims?status=flagged&limit=5"
curl -s "${R[@]}" $B/v1/claims/$C
curl -s "${R[@]}" $B/v1/claims/$C/trace

# The product's public claim link: read it, then start a claim (each start returns the claim's private link)
I=$(curl -s "${A[@]}" $B/v1/programs/$P | python3 -c 'import json,sys; print(json.load(sys.stdin)["intake_url"].rsplit("/", 1)[1])')
curl -s $B/portal/intake/$I
curl -s -X POST -H 'content-type: application/json' -d '{}' $B/portal/intake/$I

# Reviewer actions: resume_override, request_info, mark_fraud, mark_delivered
curl -s -X POST "${R[@]}" -H 'content-type: application/json' \
  -d '{"action": "request_info", "message": "Please send a clearer label photo"}' $B/v1/claims/$C/actions

# Fraud rings (Neo4j), with statuses read from Postgres
curl -s "${R[@]}" "$B/v1/programs/$P/fraud-graph?limit=50"

# Check export batches
curl -s -X POST "${A[@]}" $B/v1/payouts/exports
curl -s "${A[@]}" $B/v1/payouts/exports

# Monthly progress report: generate (optional manual boxes) and read; the PDF is at /v1/programs/{id}/mpr/{report_id}.pdf
PERIOD=$(date -u +%Y-%m)
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"incidents_after": 0}' "$B/v1/programs/$P/mpr/generate?period=$PERIOD"
curl -s "${R[@]}" "$B/v1/programs/$P/mpr?period=$PERIOD"

# Owner notices: synthetic owner list, match, drafts, simulated send
PYTHONPATH=apps/api/src uv run --project apps/api --frozen python -m recallops.synth --registrations owners.csv --program $P --n 40
curl -s -X POST "${A[@]}" -F file=@owners.csv $B/v1/programs/$P/registrations
curl -s -X POST "${A[@]}" $B/v1/programs/$P/campaign/match
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"channels": ["email"]}' $B/v1/programs/$P/campaign/drafts
curl -s -X POST "${A[@]}" -H 'content-type: application/json' -d '{"channel": "email"}' $B/v1/programs/$P/campaign/send
curl -s "${R[@]}" $B/v1/programs/$P/campaign

# The signed-in account, plans, usage, and test-mode billing
curl -s "${A[@]}" $B/v1/me
curl -s $B/v1/plans
curl -s "${R[@]}" $B/v1/usage
curl -s -X POST "${A[@]}" $B/v1/billing/checkout    # returns a Checkout URL once Stripe test keys are set

# Claim portal (token in the path)
curl -s $B/portal/$T

# Tenant isolation: another company cannot see the demo company's claim (404, not 403)
curl -s -H "X-Dev-Firm-Id: $G" $B/v1/claims/$C
```

The full contract is [docs/contracts/openapi.json](docs/contracts/openapi.json), also served at `/docs`.

</details>

## Limitations and next steps

What is not done or not good enough yet, with the next change for each:

| Limitation | Next step |
|---|---|
| The model check can deny a covered claim when the label prints a model differently from the notice (3 of 40 real CPSC label photos: an extra prefix, a model table the extractor read only partly, a field word like "ITEM") | Send a "label shows another model" result to a person, read whole model tables, and match through prefixes and field words; measure each on the real-photo block first |
| The destruction check is strict: it accepted 1 of CPSC's 3 examples of correct destruction | Revise the destruction prompt and measure it on the same block |
| A bank-transfer refund has not yet been seen to complete live. Connect account creation and onboarding links were verified on 2026-10-22; the mailed-check path was observed end to end | Complete one Connect payout in the sandbox and record its trace |
| Bank-transfer destinations are not compared across claims the way mailing addresses are | Hold a payout whose bank destination another claim already uses |
| Owner notices are drafted and "sent" without an email, mail, or SMS provider | Plug a provider in behind the send step |
| Sign-in runs on a Clerk development instance | A production Clerk instance on a custom domain |
| The website has no browser end-to-end tests; its component tests render static markup | Playwright tests for the claim portal and the review flow |
| Every claim is synthetic: the probe is assistant-designed and the 40-claim block agent-written | Claims written by people who have not seen the generator |

## Project history and further reading

RecallOps began as a four-person team project, one owner per component (backend, website, payments, login), each built in parallel against a shared API contract. The website, payments, and login components arrived as pull requests 6 to 9 and were adopted after a backend review that added regression tests; their authors are in the git history. Since October 22, 2026 one person builds every component. Each component's design is in [docs/components](docs/components), and [docs/onboarding](docs/onboarding) keeps where each owner started.

| | |
|---|---|
| [Backend design](docs/components/01-backend.md) | Schema, tenancy, the agent, tools, retrieval, the ledger, the report, the graph, and the gate tolerances |
| [Decisions log](CLAUDE.md#dead-ends-and-settled-decisions) | Every decision, dead end, and owner choice, dated, with its evidence |
| [Evaluation pre-registration](evals/adjudication/PREREG.md) | Every expectation, written before the run, and how each came out |
| [Real-photo pre-registration](evals/cpsc_photos/PREREG.md) | The vision tools on 64 photos from CPSC's recall notices |
| [Results tables](evals/results) | Extraction, adjudication, the ablation, the report reconciliation, and real photos, regenerated by `make eval` |
| [Runtime traces](docs/traces) | Real runs: claims through every path, payouts, the report, the fraud graph, sign-in, the deploy |
| [Contract changes](docs/contracts/CHANGES.md) | What changed in the API contract, item by item |
| [Going live](docs/DEPLOY.md) | Every hosted service, setting, and secret, step by step |
