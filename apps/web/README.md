# apps/web: RecallOps console and claim portal (component 2)

Next.js 16 App Router, React 19, TypeScript strict, Tailwind 4. Talks only to the RecallOps API (component 1),
always from the server: the API has no CORS, and identity is attached server-side. Spec: docs/components/02-ui.md;
onboarding: docs/onboarding/ui.md.

Status: **Phase 1B**: programs list (`/app/programs`), program overview (`/app/programs/[id]`), and the claims
queue (`/app/programs/[id]/claims`, GET /v1/programs/{id}/claims with status filter, keyset cursor, and polling),
claim detail (`/app/claims/[id]`) with reviewer actions, the agent trace (`/app/claims/[id]/trace`), and the
claimant portal (`/claim/[token]`, `/claim/[token]/status`, `/claim/connect-return`, `/claim/connect-refresh`), on
the Phase 0 scaffold (API client, proxy, shared components).

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
src/lib/queries/             react-query options (queue, claim, and trace polling)
src/lib/portal.ts            portal steps, validation, resumable drafts, plain status, payout choice, Stripe handoff
src/lib/image.ts             on-device photo compression (under 1.5 MB, longest edge 2000 px)
src/components/portal/       ClaimStepper, PhotoCapture, StatusView, NeedsInfoReply, PayoutChoice, AutoRefresh,
                             ConnectReturn
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
- **Claim portal.** Public and mobile-first, outside Clerk, with `referrer: no-referrer` and `noindex` because the
  claim token is in the URL. Six steps: confirm the product (models, where the label is, and the remedy as a
  statement, never a choice: the backend assigns it), model and codes, photos (each required kind; compressed on the
  device, previewed, uploaded at once, kept in memory for "Try again", "Retake" replaces), purchase (free-text
  retailer: `PortalProgram` has no sold-at list), your details, and review and submit. Fields resume from this tab's
  `sessionStorage` until submit; photos resume from `photos_uploaded`. A submitted claim goes to its status page,
  which is server-rendered (reading needs no JavaScript; replying does): plain-language status (a flagged or capped
  claim reads as "a person is reviewing"), denial reasons, the needs-info question with a reply and optional photo,
  the message thread, and the refund section. The payout choice (mailed check with optional mailing address, or
  Stripe onboarding) appears only when the claim is `approved` and the payout is `pending`, `onboarding`, `failed`, or
  `pending_export`; otherwise the page shows progress. A submitted claim's page refreshes every 15 s at most 8
  times, inside the link's 60 requests per hour.

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

**Payments owner and whoever runs the backend (component 3, operations)**
- Point the Connect return and refresh settings at the portal: `CONNECT_RETURN_URL=http://localhost:3000/claim/connect-return`
  and `CONNECT_REFRESH_URL=http://localhost:3000/claim/connect-refresh` (production: the web origin). They are one
  fixed URL for every claim, so before leaving for Stripe the tab stores the claim token in `sessionStorage`; the
  landing page reads it once, deletes it, and opens that claim's status page, which re-reads the API. Only an
  `https` URL on `stripe.com` is followed. Without these settings Stripe returns the claimant to `/dev/console`.
- `BILLING_RETURN_URL` still points at `/dev/console#payouts`; the billing page is not built yet.

**Backend owner (component 1)**
- `PortalView.status` answers `flagged` or `capped` to anyone holding the claim link. The portal pages show those as
  "a person is reviewing" and never echo the raw value, but the API itself discloses it.
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
