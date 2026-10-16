# Component 1: Backend (apps/api)

Owner of: data ingestion, schema, rule extraction, claim adjudication agent, duplicate and fraud detection, payouts ledger (state only; Stripe calls live in component 3), MPR generation, evals, tracing, the HTTP API, and the shared contracts the other three components code against.

Read CLAUDE.md first. This document is the contract; the invariants there win on conflict.

## 1. Scope and non-goals

In scope: everything under apps/api and evals/, plus docs/contracts/. Out of scope: UI (02), Stripe calls and billing entitlement policy (03), Clerk verification and claim-token signing (04). The backend exposes the seams those components plug into (section 9): the AuthProvider interface, the ledger and billing protocols, the webhook route stub, the OpenAPI contract, and the trace contract.

## 2. Directory layout

```
apps/api/
  pyproject.toml            uv-managed, pinned versions from CLAUDE.md
  alembic.ini, migrations/
  src/recallops/
    settings.py             pydantic-settings; every env var documented
    logs.py                 JSON-lines structured logging
    db/                     engines (app and internal roles), tenant sessions, TenantScoped repositories, RLS helpers
    models/                 SQLAlchemy models (one file per aggregate)
    text.py                 canonicalizer (source_text) shared by ingestion, extractors, labeling, evals
    ingest/                 cpsc_api.py, units.py, recallbench.py, runner.py
    rules/                  schemas.py, baseline.py, graph.py, verify.py
    claims/                 tools/ (pure code), rules_only.py, outcome_policy.py; graph.py and worker.py (M2)
    fraud/                  phash.py (M1, shared with synth); cluster.py (M2, networkx)
    payouts/                ledger.py (protocol), fake.py; state machine in M4; stripe_connect.py is component 3's
    billing/                interface.py (protocol), fake.py; component 3 owns this package
    auth/                   provider.py (AuthProvider protocol), dev.py, deps.py; component 4 owns this package
    llm/                    client.py (one wrapper), pricing.py, meter.py; model id and prices from settings
    tracing/                agent_runs and agent_steps writer, cost accounting
    api/                    FastAPI app, routers, schemas, problem details, openapi export
    synth/                  synthetic claimants, violations, rendered photos
    seed.py                 make seed
  tests/
evals/
  extraction/   golden_notices.json, sample.py, label.py (audit CLI), run.py
  adjudication/ golden_claims.json, outcome_policy.yaml, labels/, run.py
  reporting/    run.py (reconciliation, M4)
  results/      extraction.md, adjudication.md, ablation.md (M4), reporting.md (M4)
docs/contracts/
  openapi.json  generated from the FastAPI app and committed; a test fails on drift
  trace.md      agent_steps row shape and run status vocabulary
```

## 3. Data model (Postgres 16 + pgvector)

Every tenant table has firm_id (uuid, indexed) and RLS enabled and FORCED. The non-tenant tables are exactly recalls, feed_runs, and stripe_events (CLAUDE.md invariant 2). firms is scoped by its own id. firm_id is NOT NULL everywhere except agent_runs and agent_steps rows of public-archive extraction runs (graph = extract_rules with no program), enforced by a CHECK on agent_runs.

Roles (created idempotently by `make setup`, never by a migration):
- recallops_owner: runs migrations, owns every table.
- recallops_app: LOGIN, NOSUPERUSER, NOBYPASSRLS, owns nothing; the API's only role (DATABASE_URL). SELECT on public tables; SELECT, INSERT, UPDATE, DELETE on tenant tables subject to RLS; no DELETE on audit_log.
- recallops_internal: LOGIN, BYPASSRLS, owns nothing; jobs only (ingestion, seed, evals, worker; DATABASE_URL_INTERNAL).

Policies: `USING (firm_id = recallops_current_firm())` and the same WITH CHECK, where `recallops_current_firm()` returns `NULLIF(current_setting('app.firm_id', true), '')::uuid`. The context is set per transaction with `SELECT set_config('app.firm_id', :firm_id, true)`.

- recalls: id, source (cpsc|fda|fsis|nhtsa), source_id (RecallNumber, digits only, NOT NULL, non-empty), source_record_id (RecallID), recall_date (date), last_publish_date (date), title, description (canonicalized Description), url, raw (jsonb, the full API record), raw_sha256, source_text (canonical composite, section 4), source_text_sha256, canonicalizer_version, units_affected (int, nullable), units_status (ok|ambiguous|unparsed), units_text, category (Products[0].Type), retailers (text[]), manufacturers (text[]), importers (text[]), remedies (text[]), upcs (text[]), extracted_rules (jsonb), extracted_at, extractor, extractor_version, search (tsvector, generated, 'simple' config on title and source_text). Unique on (source, source_id).
- feed_runs: id, source, started_at, finished_at, status (running|done|partial|failed), fetched, inserted, updated, unchanged, failed, error.
- firms: id, name, clerk_org_id (unique, nullable until the login owner links it), stripe_customer_id, plan (enum: free|growth|enterprise), created_at.
- firm_members: id, firm_id, clerk_user_id, role (admin|reviewer), created_at. Mirror of Clerk membership for audit joins; Clerk is the source of truth.
- recall_programs: id, firm_id, recall_id (fk recalls, nullable for a manually entered recall), status (draft|open|closed), identification_rules (jsonb, schema in section 5), remedy_rules (jsonb), value_threshold_cents (int, claims above this go to a human), opened_at, closed_at.
- documents: id, firm_id, program_id, kind (notice|cap|warranty|faq|other), title, text. Chunks in document_chunks.
- document_chunks (M2 migration): id, document_id, firm_id, idx, text, embedding (vector(EMBEDDING_DIM)), search (tsvector).
- claims: id, firm_id, program_id, token_hash (unique), status (draft|submitted|needs_info|under_review|approved|denied|flagged|capped|paid|closed), claimant (jsonb: name, email, address lines, phone; synthetic only), model_claimed, serial_claimed, date_code_claimed, retailer_claimed, purchase_date, quantity, decision (jsonb: outcome, reasons[], cited_rules[], confidence), remedy (refund|repair|replacement|null), refund_cents, created_at, submitted_at, decided_at, decided_by (agent|rules_only|user id).
- claim_photos: id, firm_id, claim_id, kind (label|destruction|receipt|other), storage_path, sha256, phash (64-bit as 16 hex chars), exif (jsonb), uploaded_at.
- agent_runs: id, firm_id (nullable only for archive extraction), claim_id (nullable for extraction runs), program_id (nullable), recall_id (nullable), graph (extract_rules|adjudicate|rules_only), thread_id, status (running|done|capped|failed|waiting_human), started_at, finished_at, steps, cost_usd (numeric(10,6)), model_id, outcome (jsonb).
- agent_steps: id, run_id, firm_id (copied from the run; nullable only for archive extraction), idx (unique per run), node, thought (text), tool, args (jsonb), result (jsonb), latency_ms, tokens_in, tokens_out, cost_usd, created_at. Shape documented for the UI in docs/contracts/trace.md.
- fraud_clusters: id, firm_id, program_id, kind (same_photo|same_address|same_payout|same_serial), member_claim_ids (uuid[]), score, created_at.
- payouts: id, firm_id, claim_id (unique), amount_cents, currency, method (stripe_connect|check_export|ach_export), status (pending|pending_export|onboarding|transferred|failed|exported), stripe_transfer_id, idempotency_key (= payouts.id), attempts, last_error, created_at, updated_at.
- stripe_events: id (Stripe event id, pk), type, received_at, processed_at, payload (jsonb). Written by component 3, defined here so migrations are in one place. Not readable by recallops_app.
- mpr_reports: id, firm_id, program_id, period_start, period_end, payload (jsonb matching section 8 schema), reconciled (bool), generated_at, generated_by.
- audit_log: id, firm_id, actor (clerk user id or "agent" or "system"), action, target_type, target_id, before (jsonb), after (jsonb), at.
- usage_meters: id, firm_id, period_start, claims_adjudicated, model_cost_usd. Read by component 3 for metered billing.

Migrations: Alembic, one migration per milestone minimum, every migration reversible, hand-written SQL for RLS, policies, grants, and generated columns. Only the backend owner adds migrations; other owners request schema changes in the decisions log.

## 4. Ingestion

- cpsc_api.py: GET https://www.saferproducts.gov/RestWebServices/Recall with format=json and RecallDateStart/RecallDateEnd. Pull in 30-day windows. Timeout 30s, 3 retries with backoff on timeouts, connection errors, 429, and 5xx only, User-Agent from settings. The body must be a JSON list or the window fails loudly.
- text.py (canonicalizer v1): html.unescape once, strip tags, NBSP and other Unicode spaces to a plain space, collapse runs of spaces and tabs, keep every other character (trademark signs, dashes) as printed. source_text joins labeled sections in a fixed order (TITLE, DESCRIPTION, PRODUCTS, SOLD AT, REMEDY, HAZARD, CONTACT, UNITS). Every evidence span, golden label, and extractor reads source_text, never the raw fields.
- units.py: parse NumberOfUnits ("About 53,634" -> 53634 ok; "About 42,860 (In addition, about 340 were sold in Canada)" -> 42860 ok with the parenthetical kept in units_text; "About 1.2 million" -> 1200000 ok; several Products with different values -> ambiguous, value None; blank or no number -> unparsed, value None). Never 0 for unparsed.
- recallbench.py: optional backfill and comparison set from https://recallbench.com/data/cpsc-recalls-full.csv with Polars. Sample 30 rows inside the API archive window at a fixed seed and compare recall number (digits), date, and canonicalized title; record the mismatch rate in data/raw/SOURCES.md; a rate above 2 percent stops any backfill.
- runner.py: `python -m recallops.ingest.runner --source cpsc --since 2026-09-01` (network) or `--from-dir data/raw/cpsc` (offline replay of saved windows). Idempotent: upserts on (source, source_id) update only when raw_sha256 changed; duplicates across overlapping windows are deduplicated in memory before writing. Each record commits in its own savepoint; fetched = inserted + updated + unchanged + failed is asserted per run and written to feed_runs. Nightly via GitHub Actions cron calling POST /internal/feeds/pull with a shared secret header (M2).
- Network calls from ingestion are the only outbound HTTP in the service besides the model API and Stripe.

Runtime trace required: one full run with timestamps per window, counts inserted and updated, and total duration (docs/traces/M1-ingestion.md).

## 5. Rule extraction graph (rules/)

Input: a recalls row. Output: identification_rules and remedy_rules written to the recall_programs row when a firm opens a program, and cached on the recalls row (extracted_rules, extracted_at, extractor, extractor_version) for the archive.

identification_rules schema (pydantic):
```
models: list[str]                 exact model strings as printed in the notice
model_label_location: str | None  "sticker on the bottom of the laminator"
serial_ranges: list[SerialRange]  {prefix: str | None, start: str | None, end: str | None, raw: str}
date_codes: list[DateCodeRule]    {format: str, start: str | None, end: str | None, raw: str}
sold_at: list[str]                retailers, from the SOLD AT section of source_text
sold_from: date | None            first day of the stated month when only a month is given
sold_to: date | None              last day of the stated month when only a month is given
price_range_cents: PriceRange | None   {min_cents, max_cents}
units_affected: int | None
evidence: list[Span]              {field, quote, start, end} into source_text
```
remedy_rules schema:
```
remedy_type: Literal["refund","repair","replacement","multiple"]
options: list[RemedyOption]       {type, steps: list[str], proof_required: list[ProofKind], payout: {method: check|original_payment|store_credit|null, amount_rule: str}}
destruction_instruction: str | None   "cut the power cord and submit a photo"
registration_url: str | None
contact: str | None
evidence: list[Span]
```

Two extractors, both required:
1. rules/baseline.py: regex and heuristics only. Model numbers: tokens matching the notice's own phrasing patterns ("model number X", "Model No. X", "models X, Y and Z"). Serial ranges: "serial numbers beginning with", "through", "between". Date codes: "date code", "manufactured between". Retailers: the SOLD AT section. Destruction: sentences containing cut, destroy, disassemble, mark.
2. rules/graph.py: LangGraph graph. Nodes: load_notice -> draft_rules (model call through llm/ with the schema as the output format) -> verify_spans (every extracted value must appear verbatim in source_text; values that do not are dropped and logged) -> finalize. No tools beyond source_text. One model call per notice, capped at 2 on verification failure, and subject to the meter's pre-call check. Every node writes an agent_steps row. In M1 the graph is compiled without the Postgres checkpointer (single pass, no interrupts).

Eval (evals/extraction): M1 uses 40 notices drawn by a seeded stratified sample across 2024 to 2026 recall dates and Products[].Type categories, labeled blind before either extractor existed, marked "model-labeled, unaudited". The 15 baseline unit-test notices and the 20 synthetic-source notices are subsets of the 40. M2 extends the set to 100 owner-audited notices with the labeling CLI (evals/extraction/label.py). Metrics per field: exact-match accuracy for scalar fields, set F1 for list fields, span-grounded rate (share of extracted values whose evidence span is correct). Headline "field accuracy" is the unweighted mean of the per-field scores over the headline fields listed in evals/extraction/PREREG.md, committed before the first eval run. Report both extractors side by side, on all notices and on the notices outside the 15 unit-test notices. Tolerance for the deploy gate: no headline field accuracy drops more than 2 points versus the committed table.

Kill criterion from CLAUDE.md applies here; the M1 verdict is provisional.

## 6. Claim adjudication graph (claims/)

Trigger: claim status becomes submitted. A polling worker (`python -m recallops.claims.worker`) picks up submitted claims, one at a time per firm with a row lock, and runs the graph with thread_id = claim id so a crash resumes from the checkpoint. (M2)

Nodes and tools (every tool is a pydantic-typed function; every node writes an agent_steps row):
1. load_context: program rules, claim fields, photo metadata. No model call.
2. read_label_photo (tool, vision): returns {model_text: str | None, serial_text: str | None, date_code_text: str | None, confidence}. Cost-capped; one call.
3. check_model (tool, pure code): compares claim.model_claimed and label photo text against identification_rules.models with normalization (case, hyphens, spaces). Returns match | mismatch | ambiguous with the rule cited.
4. check_serial (tool, pure code): only if serial_ranges is non-empty. Lexicographic and numeric range logic with prefix handling. Returns in_range | out_of_range | not_provided.
5. check_date_code (tool, pure code): only if date_codes non-empty.
6. check_purchase (tool, pure code): retailer in sold_at, purchase_date within sold window, receipt photo present if proof_required includes receipt.
7. check_destruction_photo (tool, vision): given remedy_rules.destruction_instruction and the destruction photo, returns {complies: bool, observed: str, confidence}. Only when the remedy requires it.
8. check_duplicates (tool, pure code): phash distance to other photos in the program (threshold 6 bits), sha256 exact matches, same claimant address or email, same payout destination, same serial already claimed. Returns the list of hits with cluster ids.
9. retrieve_policy (tool, keyword + vector): fetches relevant chunks from the program's documents for edge cases (e.g., what the corrective action plan says about receipts). The ablation turns this off.
10. decide: model call that must return {outcome: approve|deny|needs_info|flag, reasons: list[str], cited_rules: list[str], question_for_claimant: str | None, confidence}. Every reason must cite a rule id or a tool result id; uncited reasons are rejected and the node retries once.
11. route: approve -> ledger.create_pending (payouts protocol) and status approved; deny -> status denied with reasons; needs_info -> status needs_info with the question, portal shows it; flag -> status flagged and LangGraph interrupt waiting for a reviewer; refund_cents above program.value_threshold_cents -> interrupt regardless of outcome.

Caps (invariant 4): 8 steps, 0.02 USD, 60 seconds. The cost meter in llm/ raises CapExceeded before a call that would cross a cap; the graph catches it, writes status capped, and interrupts.

Rules-only adjudicator (claims/rules_only.py, M1 deliverable and the baseline row in the ablation table): pure-code tools only, in fixed order (check_model on the typed model_claimed, check_serial, check_date_code, check_purchase, check_duplicates with sha256 and pHash), destruction photo presence only, no vision, and a fixed decision table that implements evals/adjudication/outcome_policy.yaml. It writes agent_runs (graph = rules_only) and one agent_steps row per tool, so its steps, latency, and zero cost come from the same trace path. M2 adds the ablation row rules_plus_vision (the same table fed by tools 2 and 7).

Reviewer actions (called by the UI through the API): resume with override outcome, request more info, mark fraud. Each writes audit_log with before and after.

Runtime trace required: one claim through every path (approve, deny, needs_info, flag, capped), with step timestamps and costs, stored in docs/traces/M2-adjudication.md.

## 7. Synthetic claim generator (synth/)

`python -m recallops.synth --program <id> --n 500 --seed 42 --violations evals/adjudication/planted.yaml`

Generates claimants (fake names and addresses from a deterministic generator, never real people: emails only at example.com, example.net, or example.org; phones only in 555-0100 to 555-0199), model and serial values drawn from the program's rules, and photos. Photos (Pillow): programmatically rendered label images (model and serial text with distractor fields on a seeded procedural background, with blur, rotation, and JPEG quality drawn per claim from a stream independent of the violation) and destruction images (a rendered product silhouette with or without the instructed destruction: severed cord, cut fabric, or RECALLED marking), so the vision tools have something real to read without any real consumer imagery. Fonts are Pillow's bundled default only. Planted violations with labels: wrong_model, serial_out_of_range, date_code_out_of_range, retailer_not_listed, purchase_before_window, no_receipt_when_required, destruction_not_shown, duplicate_photo, duplicate_address, duplicate_serial, duplicate_payout. Each generated claim carries its ground-truth label and expected outcome (from outcome_policy.yaml) in a sidecar file under evals/adjudication/labels/. Nothing under apps/api/src reads the sidecars.

The 300-claim golden set is generated from 20 golden-labeled notices (a subset of the 40), using the golden rules rather than extracted rules. In M2, 60 of those claims are hand-reviewed by the owner and 40 adversarial ones are written by a classmate who has not seen the generator. The classmate set is held out and reported separately.

## 8. MPR generator (mpr/)

Aggregation in SQL over claims and payouts for a program and period, into exactly these boxes (from the CPSC instructional guide):
- total_units_affected (from the program, constant)
- corrections_this_period, cumulative_corrections (approved claims with remedy delivered: paid, or repair/replacement marked delivered)
- units_with_manufacturer, distributor, retailers, consumers (manual inputs with defaults; consumers = total minus the others)
- incidents_before, incidents_after, injuries_before, injuries_after, deaths_before, deaths_after (manual inputs)
- notified_phone_text, notified_email, notified_mail (from M5 campaign tooling; manual until then)
- contacts_phone, contacts_email, contacts_mail, contacts_registration_card (portal submissions count as contacts_email by default; manual overrides allowed)
- homepage_posting (bool, explanation if false), website_hits, social_posts (jsonb), marketplace_monitoring (jsonb)

Reconciliation test: regenerate the report from scratch and compare with a second computation written independently in Polars over a claims export; the two must agree on every count. Zero tolerance. This is evals/reporting/run.py.

The completion forecast was killed on 2026-10-16: the public MPR spreadsheet holds one cumulative row per recall per snapshot, not per-recall monthly rows (CLAUDE.md decisions log).

## 9. HTTP API (api/)

All firm routes require `current_firm` (auth/deps.py), which resolves a FirmContext {firm_id, user_id, role} through the configured AuthProvider: the dev provider in M1 and M2 (AUTH_PROVIDER=dev, headers X-Dev-Firm-Id, X-Dev-Role, optional X-Dev-User-Id), the login owner's Clerk provider from M3. Portal routes require a valid claim token through the ClaimTokenVerifier protocol in auth/ (dev verifier in M1 and M2: tokens of the form dev.<claim uuid>; the login owner's HMAC verifier from M3). Every route is in docs/contracts/openapi.json with example payloads; routes not yet implemented return 501 with a problem detail and keep their contract.

Firm routes:
- GET /v1/recalls?q=&since=  search the public archive (full text)
- POST /v1/programs  {recall_id | manual fields} -> runs extraction, returns program with rules for review
- GET /v1/programs, GET /v1/programs/{id}, PATCH /v1/programs/{id} (edit rules, threshold, status)
- GET /v1/programs/{id}/claims?status=&cursor=  queue with filters
- GET /v1/claims/{id}  claim, photos (signed URLs), decision, run summary
- GET /v1/claims/{id}/trace  agent_steps for the latest run
- POST /v1/claims/{id}/actions  {action: resume_override|request_info|mark_fraud, payload}
- POST /v1/programs/{id}/claims/issue  {n} -> issues n claim tokens (links) for distribution
- GET /v1/programs/{id}/mpr?period=YYYY-MM, POST .../mpr/generate, GET .../mpr/{id}.pdf (M4, simple HTML to PDF)
- GET /v1/usage  current period meters (read by billing UI)

Billing routes (component 3 replaces the stubs; listed so the UI can code against them):
- POST /v1/billing/checkout, POST /v1/billing/portal

Portal routes (token in path, rate-limited):
- GET /portal/{token}  program public info, required steps, current status, pending question
- POST /portal/{token}/submit  fields
- POST /portal/{token}/photos  multipart, kind, stored to Supabase Storage, phash computed server-side
- POST /portal/{token}/answer  reply to needs_info
- POST /portal/{token}/payout  payout destination (Connect onboarding link handoff lives in component 3)

Internal:
- POST /internal/feeds/pull  shared secret header
- POST /internal/stripe/webhook  (component 3 implements; registered here as a stub that verifies nothing and returns 501)
- GET /healthz, GET /readyz (checks DB and, from M2, that the worker heartbeat is under 5 minutes old)

Responses are pydantic schemas; errors are RFC 7807 problem details.

## 10. Evals and the ablation table (evals/)

`make eval` runs all of the following and rewrites evals/results/*.md:
- extraction: baseline vs graph, per field, on the golden notices.
- adjudication: rows = rules_only, rules_plus_vision (M2), single_pass (graph with decide only, no loop), loop_no_retrieval, loop_keyword_only, loop_full, loop_full_cheap_model; columns = approval accuracy, fraud precision, fraud recall, needs_info precision, share routed to human, mean steps, mean cost USD, p50 seconds. On the 300-claim set, with the 40 classmate claims reported as a separate block.
- reporting: reconciliation pass or fail with the count diff (M4).

Deploy gate (.github/workflows/deploy.yml, M4): runs make eval on the committed golden sets with recorded model outputs (replay mode, no live model calls) and fails if any headline metric regresses more than its tolerance: extraction fields 2 points, adjudication accuracy and fraud F1 0.02, reconciliation any discrepancy.

## 11. Tasks in order (for Claude Code)

M1 (backend only, plus the team seams)
0. Dependency gate: pyproject with exact pins, uv lock, uv sync; API-shape probe tests for the pinned libraries.
1. Scaffold apps/api with uv, ruff, mypy, pytest; docker-compose.yml at the root; role bootstrap in make setup; Alembic initial migration for every section 3 table except document_chunks; RLS, FORCE, policies, grants; set_config helper; database-level cross-tenant test as recallops_app.
2. settings.py with every variable; .env.example.
3. text.py canonicalizer, units.py, cpsc_api.py, and runner with tests on recorded fixtures (data/samples/cpsc_sample.json, 51 records from Sept 15 to Oct 16, 2026); Recall Bench comparison.
4. Golden set first: seeded stratified sample of 40 notices and blind labels (before any extractor exists); PREREG.md. Then rules/baseline.py with unit tests on 15 of the 40.
5. llm/ wrapper with the cost meter (pre-call projected cost, run caps, global budget) and model id and prices from settings; fake-transport tests; zero live calls in M1.
6. rules/graph.py with the span verification step; fake-model test; agent_steps rows per node.
7. evals/extraction runner and the labeling CLI for the owner's audit.
8. synth generator with planted violations and rendered photos; fraud/phash.py.
9. claims/tools (pure code), outcome_policy.yaml, claims/rules_only.py; evals/adjudication baseline row.
10. Seams: OpenAPI contract for every route; auth seam with the dev provider; payments protocols, fakes, and the webhook stub; read routes over the seeded database; make seed, make api, README curl calls; docs/contracts/trace.md; CI for the backend half; CODEOWNERS; onboarding files.
11. make eval; first results tables committed.

M2
12. llm/ vision call path; live model calls only after the meter's estimate is approved.
13. claims/graph.py with all tools, caps, interrupt, checkpointer; worker; agent_steps tracing.
14. fraud/: clusters, networkx connected components.
15. API routes implemented behind the M1 contract (programs, claims, trace, portal).
16. Golden set extended to 100 owner-audited notices; document_chunks and retrieve_policy.
17. Runtime trace docs/traces/M2-adjudication.md.

M3 (backend part)
18. Clerk provider wiring (component 4 implements the provider), API-level cross-tenant test, audit_log on reviewer actions, usage_meters, entitlement hook calls.

M4
19. payouts ledger state machine and the Stripe hook points (component 3 calls ledger.mark_transferred etc.).
20. mpr/ aggregation, reconciliation eval, PDF export.
21. Full ablation run; deploy gate.

## 12. Acceptance tests (must exist and pass)

- The tenancy test connects as recallops_app and first asserts the role is neither superuser nor owner of any table. Then: under firm A's context, raw SQL returns zero rows of firm B in every tenant table, UPDATE and DELETE of B's rows affect zero rows, and an INSERT with B's firm_id is rejected; with no context set, every tenant table returns zero rows. From M2: a request with firm A's dev headers (from M3: Clerk token) to GET /v1/claims/{id} for firm B's claim returns 404.
- A meta-test asserts every table with a firm_id column has RLS enabled and forced.
- Replaying the same claim submission twice creates one claim.
- A claim whose label photo text does not match any rule model is denied with a cited rule.
- A claim with serial outside range is denied only when serial_ranges exist; otherwise serial is ignored.
- Two claims with photos within phash distance 6 are both flagged and share a cluster id.
- A run that would exceed 0.02 USD stops with status capped and an interrupt; resuming after reviewer override completes without re-running prior steps.
- The meter refuses a call whose projected cost would cross the run cap or the global budget, and the fake transport records zero requests.
- MPR reconciliation passes on a program with 500 synthetic claims in mixed states.
- make eval completes in replay mode without network access.
- The committed docs/contracts/openapi.json equals the document generated from the app.
