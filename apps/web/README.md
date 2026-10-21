# apps/web: RecallOps console and claim portal (component 2)

Next.js 16 App Router, React 19, TypeScript strict, Tailwind 4. Talks only to the RecallOps API (component 1),
always from the server: the API has no CORS, and identity is attached server-side. Spec: docs/components/02-ui.md;
onboarding: docs/onboarding/ui.md.

Status: **Phase 1B**: programs list (`/app/programs`), program overview (`/app/programs/[id]`), and the claims
queue (`/app/programs/[id]/claims`, GET /v1/programs/{id}/claims with status filter, keyset cursor, and polling),
claim detail (`/app/claims/[id]`) with reviewer actions, and the agent trace (`/app/claims/[id]/trace`), on the
Phase 0 scaffold (API client, proxy, shared components).

## Run

```
cd apps/web
cp .env.example .env.local      # server-only settings; dev auth against an API running AUTH_PROVIDER=dev
npm ci
npm run dev                     # http://localhost:3000 (the API's PORTAL_BASE_URL points here)
```

| script | does |
|---|---|
| `npm run gen:api` | regenerate `src/lib/api-types.ts` from `docs/contracts/openapi.json` (or `-- <url>` for a running API; `-- --check` fails when stale) |
| `npm run typecheck` | `tsc --noEmit` (what CI runs) |
| `npm test` | `vitest run` (what CI runs) |
| `npm run build` | production build (telemetry disabled in every script) |

## Layout

```
scripts/gen-api.mjs          contract -> zod generator (no third-party codegen dependency)
src/lib/api-types.ts         GENERATED zod schemas and types; never edit by hand
src/lib/api/config.ts        server settings (RECALLOPS_API_URL, timeout)
src/lib/api/auth-seam.ts     console identity: dev headers today, Clerk later (component 4 swaps this one function)
src/lib/api/forward.ts       proxy rules: path allowlist, header allowlists (unit-tested)
src/lib/api/backend.ts       server-side API calls with timeouts and typed errors; apiJson() parses with zod
src/lib/api/problem.ts       RFC 7807 -> { status, title, detail, correlationId, retryAfterSeconds } + human sentence
src/lib/api/queries.ts       page loaders returning { ok, data } or { ok: false, problem } (pages render the error state)
src/lib/status.ts            labels for claim, run, payout, outcome, and program statuses; unknown -> "In progress"
src/lib/programs.ts          figures derived from Program fields only (filed, awaiting review, corrected)
src/lib/format.ts            counts, cents, UTC dates and times, percents
src/lib/claims.ts            queue URL state, API path, review classification
src/lib/claim-detail.ts      reviewer action rules (mirrors claims/review.py), photo readings and duplicate hits from
                             the trace, citations, cap use, polling intervals
src/lib/api/client.ts        browser fetch through /api, validated with zod
src/lib/queries/             react-query options (queue polling)
src/app/api/[...path]/       same-origin proxy for client components: /api/v1/* and /api/portal/* only
src/components/              AppShell, StatusBadge, ProblemAlert, LoadingState, EmptyState, ProgramsTable, ProgramOverview,
                             ClaimsQueue (client), ClaimsTable, QueueControls (filter, review summary, pager),
                             ClaimPanels, ReviewerActions (client), ClaimDetailLive (client), TraceView, TraceLive
tests/                       vitest (node environment; components rendered with react-dom/server)
```

## Decisions in this component

- **All API traffic goes through the Next server.** Server components call `apiJson()`. Client components call
  `/api/v1/...` or `/api/portal/...`, which forwards only `GET`, `POST`, and `PATCH` to `/v1/*` and `/portal/*`.
  `/internal/*`, `/dev/*`, and the docs are refused. Browser `X-Dev-*`, `Authorization`, `Cookie`,
  `X-Forwarded-*`, and `Host` headers are dropped; the server attaches the identity. Every request gets an
  `x-request-id`, which the API uses as its `correlation_id`.
- **Lenient responses, strict requests.** Generated response schemas keep unknown fields and accept unknown enum
  values (CHANGES.md item 5). Schemas used only in request bodies are strict and keep the contract's limits.
- **Dev auth fails closed.** The console gets an identity only with `RECALLOPS_WEB_AUTH=dev`, a UUID firm id, and an
  `admin` or `reviewer` role, and never when `VERCEL_ENV=production`. None of the settings is `NEXT_PUBLIC_`.
- **Packages beyond the CLAUDE.md pin list**, pinned exactly because the pinned stack cannot build or test without
  them: `@tailwindcss/postcss` 4.3.3 (Tailwind 4's PostCSS plugin), `@types/react` and `@types/react-dom` 19.3.0,
  `@types/node` 22.20.5 (matches Node 22), `vite` 8.3.4 (vitest 5's peer). Not installed yet: Clerk (component 4),
  Stripe and Supabase (no browser use in the current flows), Playwright (Phase 2).
- **Toolchain:** the lockfile was written by npm 10.9.4 (the pin), and CI uses Node 22.22.0. Local verification ran on Node 24.15.0.

- **Program figures come from the contract only.** "Claims filed" is every claim past `draft` (an issued link
  not yet submitted); "Awaiting review" is `flagged + under_review + capped`; "Corrected" is `paid + closed` as a
  share of `units_affected`, labelled as indicative because the MPR is the authoritative count. Next MPR due,
  model spend per program, and charts are out of the MVP (no endpoint).
- **Claims queue.** The server renders the first page (it works without JavaScript); in the browser, react-query
  polls the page and the program's counts every 10 s through `/api`, skips ticks while the tab is hidden, refreshes
  as soon as it is shown, and keeps the last good page on screen when a poll fails (no retry bursts; the next tick
  retries). Filters and pages are URL state (`?status=&cursor=`); a filter change returns to page one. The cursor is
  forward-only (keyset), so the pager offers "Next page" and "First page"; the browser's Back button returns to the
  previous page. A `?status=` outside the contract or a cursor the API could not have issued is dropped before the
  call, with a note. Claims in `flagged`, `under_review`, or `capped` carry a marker and the text "Needs a
  reviewer", and their statuses lead the filter chips. The table shows "Created", not "Submitted": `ClaimSummary`
  has `created_at` and `decided_at` but no `submitted_at`.
- **Claim detail and trace.** Photo bytes load through `/api` (the photo URL is an API path; anything else is not
  rendered). Each photo's reading comes from the latest run's steps (`read_label_photo`, `check_destruction_photo` or
  `check_destruction_presence`, `check_purchase`); duplicate hits from `check_duplicates` sit beside the Neo4j
  clusters. Decision citations `T1`, `T2`, ... link to the cited trace steps. Reviewer actions follow
  `claims/review.py`: approve, deny, request info, and mark fraud on flagged, under_review, capped, needs_info,
  approved, and denied claims; mark delivered only on an approved repair or replacement; nothing while an agent run
  is running. Each action has an inline confirmation (no browser dialogs). When the action resumes a paused run, the
  page polls for up to 60 s until the worker applies it. The trace polls every 1.5 s while its run is running.
- **CSRF.** The proxy refuses a `POST` or `PATCH` whose `Origin` or `Sec-Fetch-Site` says it came from another site,
  because writes carry the server's identity.

## Notes for other owners

**Login owner (component 4)**
- Next.js 16.4.2 deprecates `middleware.ts` in favour of **`proxy.ts`**. The installed build warns "The
  "middleware" file convention is deprecated. Please use "proxy" instead" and refuses a project that has both.
  `.github/CODEOWNERS` assigns `/apps/web/src/middleware.ts` to you; `/apps/web/src/proxy.ts` needs the same
  entry, which is a backend-owned file. This component created neither file.
- The console identity seam is `consoleAuthHeaders()` in `src/lib/api/auth-seam.ts` (it sits outside
  `src/lib/auth/` so it does not touch your paths). Replace its body with the Clerk session token, or point it at
  your `apiFetch` helper. Everything else in apps/web calls through it. `ClerkProvider` slots into
  `src/app/layout.tsx` (marked), and `/claim/*` must stay outside Clerk.

**Backend owner (component 1)**
- Two documentation examples in `docs/contracts/openapi.json` omit required nullable fields: `Program` has no
  `closed_at`, and `Trace.steps[0]` has no `thought`. The API itself serializes nulls, so this does not affect
  runtime; `tests/api-types.test.ts` tolerates exactly these two gaps and asks for the entry to be removed when they
  are fixed.
- `GET /v1/programs/{id}/claims` answers 400 "invalid cursor" for a cursor it cannot decode, but the contract lists
  only 401, 403, 404, and 422 for that route. The UI handles the 400; the contract could list it.
- `Decision` in the API has six fields (`outcome`, `reasons`, `cited_rules`, `confidence`, `routed_to_human`,
  `decided_by`) and forbids extras, so `hold_reason`, `reopened_from`, `duplicate_found_later`, `duplicates_ack`, and
  `question_for_claimant` (CHANGES.md item 6) never reach the UI. The console keys reviewer actions on the claim
  status as the contract says, but cannot show why an `under_review` claim is held or that a claim was reopened.
- The packages listed above under "beyond the pin list" need a decisions-log entry if the pin list is meant to cover
  apps/web devDependencies.
