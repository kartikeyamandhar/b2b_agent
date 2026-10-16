# Kickoff prompt (session 1, 2026-10-16)

The prompt that started milestone M1, saved verbatim, followed by the two amendments issued in the same session. Later sessions should read CLAUDE.md first; this file records how the work was framed.

## Original kickoff

Read CLAUDE.md in full. Then read docs/components/01-backend.md. Do not read the other component documents yet.

You are starting milestone M1 of RecallOps. Before writing any code or running any command, do the following in order and stop for my approval at the end.

1. Repository map. State what exists in the folder now (it should be CLAUDE.md, docs/, and data/raw/mpr.xlsx) and the exact directory tree you will create for M1, matching the layout in CLAUDE.md.

2. Data check first. Using Python with Polars, open data/raw/mpr.xlsx read-only and report: sheet names, column headers, row count, and whether rows are one per recall per month. Quote the first five rows. Then say in one line whether the completion forecast is buildable or killed under the CLAUDE.md kill criterion. Running this script counts as executing repository code; ask me before running it, then run it once approved.

3. Red-team review of the M1 plan in 01-backend.md section 11. Answer each of these in two to four sentences, with a concrete fix where the answer is a problem:
   a. Where can the CPSC API parser silently drop or corrupt a record (units text, blank Model fields, date formats, HTML entities in descriptions)?
   b. Where could the extraction golden set leak into the extractor (the baseline's guesses are shown during labeling; how do we keep that from biasing labels)?
   c. How will the synthetic photos be rendered so the vision tool is tested without real consumer imagery, and what makes the destruction check non-trivial?
   d. What makes the rules-only adjudicator a fair baseline rather than a strawman?
   e. Which tables need RLS in M1 even though there is no login yet, and how will the tenancy test run without Clerk?
   f. How is the cost meter enforced before the first model call rather than after?
   g. Which pinned versions in CLAUDE.md are likely to have breaking changes versus your training data (LangGraph 1.x checkpointer API, SQLAlchemy 2.1, Polars 2.0, pydantic 2.14), and how will you confirm the current API shape before writing code against it?

4. Proposed M1 execution plan as a numbered task list with, for each task, the files it creates, the test that proves it, and the command I will run to verify. Keep the order from 01-backend.md section 11 unless you have a stated reason.

5. Stop. Print "AWAITING GO" and wait. Do not scaffold, install, or run anything else until I reply with "go".

Standing rules for every session in this repository:
- Tier the action, not the artifact. Small reversible edits: just do them and show the diff. Anything that runs code, touches the network, writes outside the repo, or changes an interface: say what you are about to do and wait for approval unless I have already approved it this session.
- "Done" means evidence: the executed command, its output, the test summary, or the diff. Never describe a result you did not observe.
- Every timing, async, scheduling, or UI state change ships with a runtime trace file under docs/traces/.
- Append to the decisions log in CLAUDE.md whenever something is tried and abandoned. Never re-propose a logged dead end without new evidence.
- No AI attribution trailers in commits. No employer data or context. No real consumer data. Stripe test mode only. Polars, never pandas. Python 3.13 with full type hints, ruff, mypy strict.
- If cumulative LLM spend passes 8 USD, stop and report.
- End each milestone with the Definition of done checklist from CLAUDE.md, each item answered with pasted evidence.

## Amendment 1: FREE-HAND grant and data acquisition (same session)

Correction to the plan: there is no data on this machine and there will not be any placed by hand. You acquire everything yourself. This session is a FREE-HAND grant for milestone M1 under the limits below; log the grant in the decisions log.

Data acquisition, approved now, no further asking needed for these exact actions:
1. Download the CPSC monthly progress report spreadsheet from the link on https://www.cpsc.gov/Data/Monthly-Progress-Reports (the file named "Download MPR Data - Recalls August 1, 2022 to Present") to data/raw/mpr.xlsx. Record the URL, the download timestamp, the byte size and the sha256 in data/raw/SOURCES.md. If the page or link has changed, search for the current link on cpsc.gov and record what you found; do not substitute another dataset.
2. Call the CPSC recall API (https://www.saferproducts.gov/RestWebServices/Recall with format=json) for RecallDateStart=2026-09-15, RecallDateEnd=2026-10-16 and save the raw response as data/samples/cpsc_sample.json. Then pull the full archive in 30-day windows from 2022-08-01 to today into data/raw/cpsc/ as one JSON file per window, with a 2-second pause between calls and a User-Agent of "RecallOps research (student project)". Record counts in SOURCES.md.
3. Download https://recallbench.com/data/cpsc-recalls-full.csv to data/raw/recallbench/ as the backfill comparison set. Sample 30 rows and compare against the API records; write the mismatch rate to SOURCES.md.
4. PyPI and npm downloads for the pinned packages, plus fastexcel 0.21.0, Pillow 12.3.0, numpy 2.5.4, PyYAML 6.0.3, pytest-cov 7.1.0, which you add to the pins in CLAUDE.md.
5. git init, .gitignore (Python, Node, .env, data/raw/, .DS_Store), remote kartikeyamandhar/b2b_agent. Commit locally as you go. No push without asking.
6. Save my kickoff prompt as docs/KICKOFF_PROMPT.md.

Doc fixes to apply before any code, then one decisions-log entry titled "Session 1 red-team fixes":
- Cost meter moves into M1 as a task between tasks 4 and 5; every model call goes through the llm/ wrapper and the meter checks projected cost before the call.
- agent_steps gets firm_id (nullable only for public-archive extraction runs). Non-tenant tables, listed explicitly in invariant 2: recalls, feed_runs, stripe_events.
- Database roles: recallops_app (no BYPASSRLS, not a table owner) for the API, recallops_internal (BYPASSRLS) for jobs, a separate migration role owns tables, FORCE ROW LEVEL SECURITY on every tenant table. The tenancy test connects as recallops_app and asserts it is neither superuser nor owner before checking isolation.
- docker-compose.yml at the repo root; .github/workflows/ at the root; infra/ keeps render.yaml only.
- recalls gets extracted_rules jsonb, extracted_at, extractor, extractor_version.
- Rules-only baseline uses only pure-code tools (check_model on the typed model, check_serial, check_date_code, check_purchase, check_duplicates); destruction photo presence only; no vision in M1. M2 adds an ablation row rules_plus_vision.
- document_chunks is not created in M1; choose EMBEDDING_MODEL_ID and EMBEDDING_DIM with LLM_MODEL_ID at phase 0, log them, create the table in M2.
- M1 golden set is 40 notices labeled blind, extended to 100 in M2; the 15 test notices and 20 synthetic-source notices are subsets of the 40. Since there is no human labeler in this session, produce the 40 labels yourself with the two extractors hidden from the labeling pass, mark the set as "model-labeled, unaudited" in evals/results, and leave a labeling CLI ready for my audit pass later.
- Mode section: at most 3 sub-agents per task without approval.

FREE-HAND limits for this session. Stop and print "STOPPED: <reason>" if any of these would be crossed:
- Any git push, any deploy, any account creation, any secret written anywhere but .env.
- Cumulative LLM spend above 2 USD for M1 (the meter is the source of truth; until it exists, zero model calls).
- Any package not pinned in CLAUDE.md after the additions above.
- Any network call outside the six approved sources: cpsc.gov, saferproducts.gov, recallbench.com, pypi.org, registry.npmjs.org, github.com.
- Any schema or interface change that contradicts an invariant in CLAUDE.md.
- Any test that fails after two fix attempts: stop and show the failure rather than weakening the test.

Then proceed without waiting: finish the red-team answers, write the M1 task list with these changes folded in, execute M1 in order, and end with the Definition of done checklist from CLAUDE.md with pasted evidence for every item, the forecast verdict from the spreadsheet, and the first numbers tables in evals/results/. If the forecast is killed by the kill criterion, say so in the decisions log and remove it from M4 in CLAUDE.md.

## Amendment 2: team scope (same session)

Scope change: this is a group project, not solo. I own the backend only. Three teammates own the UI (02-ui.md), payments (03-payments.md) and login (04-login.md). You build only apps/api and evals/, and you set the other three up to work in parallel against stable contracts. Update CLAUDE.md: Intent says "team of four, one owner per component"; the milestones keep their dates but each milestone lists backend deliverables only; add a section "Working in a team" with the rules below.

What you build in M1 and M2 so the others are unblocked:
1. Contracts first. Generate and commit the OpenAPI document for every route in 01-backend.md section 9, including the portal and internal routes, with example payloads, before the routes are implemented. The UI owner codes against this file; breaking it needs a logged decision and a message to the team.
2. Login seam. Implement `current_user` and `current_firm` as FastAPI dependencies behind an interface, with a development provider that reads X-Dev-Firm-Id and X-Dev-Role headers when AUTH_PROVIDER=dev and refuses to load when any Stripe or Clerk live key is present. The login owner replaces the provider with the Clerk one; nothing else in the backend changes. Tenant scoping, RLS and the cross-tenant test are mine and ship in M1 using the dev provider.
3. Payments seam. Define the ledger interface in payouts/ (create_pending, mark_onboarding, mark_transferred, mark_failed, mark_exported) and the billing interface (check_entitlement, record_usage) as plain Python protocols with a fake implementation that always allows and logs. Register the webhook route as a stub that verifies nothing and returns 501 until the payments owner replaces it. Leave stripe_events and payouts tables in my migrations as specified.
4. UI seam. Provide `make seed` that loads one real recall from data/samples, opens a program, generates 500 synthetic claims, runs the rules-only adjudicator, and leaves the database in a state the UI owner can build every screen against. Provide `make api` to run it. Document both in README with the exact curl calls for every route.
5. Trace contract. Document the agent_steps row shape and the run status vocabulary in docs/contracts/trace.md, since the UI trace viewer renders it.

Repository rules for four people:
- Trunk-based: main is protected in spirit; everyone works on short branches named <owner>/<topic> and opens a pull request; CI runs ruff, mypy, pytest with coverage for apps/api and vitest for apps/web on every pull request. Set up .github/workflows/ci.yml now for the backend half; leave a clearly marked job for the web half.
- CODEOWNERS: apps/api and evals to me, apps/web to the UI owner, apps/api/src/recallops/billing and payouts/stripe_connect.py to the payments owner, apps/api/src/recallops/auth and the Clerk parts of apps/web to the login owner. Use placeholder GitHub handles I will fill in.
- Each component owner gets a two-page onboarding file docs/onboarding/<component>.md: what exists, what the seam is, how to run the backend locally, which tests must stay green, and the three commands to verify their work. Write all three.
- Migrations: only I add them; others request schema changes by a note in the decisions log.
- Nobody runs evals that call a model except through the meter, and the M1 budget stays 2 USD total.

After the scope change, finish the red-team answers for the backend only, rewrite the M1 task list for backend-only scope with the four seams added, execute M1 under the same FREE-HAND limits, and end with the Definition of done evidence plus a short "handoff" section listing exactly what each teammate can start on Monday.
