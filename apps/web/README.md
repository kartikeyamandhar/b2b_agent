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
src/lib/api/auth-seam.ts     console identity: the Clerk session token (clerk) or the dev headers (dev); else fail closed
src/lib/auth/                Clerk mode rules (routes, matcher, roles, console access) and the server-side session lookup
src/proxy.ts                 Next 16 proxy: clerkMiddleware on the console in Clerk mode; never on /claim or /api/portal
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

## Design system: Ink & Paper (baseline for every screen)

Approved by the UI owner on 2026-10-21. New screens use these tokens and classes; do not add one-off colors or fonts.

- **Tokens** live only in `src/app/globals.css`: paper `#F8F7F4` (page), white surfaces (cards, tables, inputs), ink
  `#222222` (text, primary buttons), secondary ink `#56554F`, warm border `#E4E1DB`, copper `#A56D45`. Keep the page
  about 80% neutral, 15% ink, 5% copper.
- **Copper** marks selection, progress, link underlines, and focus only. It is about 4.2:1 on white, so never use it
  for small text.
- **Status colors are separate from the brand:** green for approved, completed, paid; amber for flagged, pending
  review, warnings; red for denied, errors, destructive actions (`success`, `warn`, `danger` tokens and
  `StatusBadge` tones).
- **Type:** DM Sans for everything operational (navigation, buttons, forms, tables, KPI values); Newsreader only for
  page headings (`page-title`, 30 px, 26 px on phones), portal recall titles, status headlines, and empty states.
  Section headings use `section-title` (18 px), headline figures `kpi-value` (28 px), small labels `eyebrow`.
  Both fonts are self-hosted from npm (Fontsource variable, OFL-1.1, pinned 5.3.0): no font CDN at build or run time.
- **Components:** cards are `rounded-lg border border-line bg-surface`; primary buttons solid ink (`bg-accent
  text-bg`), secondary buttons white with `border-line-strong`; destructive actions red outline. No gradients, glass,
  heavy shadows, neon, or decorative oversize elements.
- **Checks for any new screen:** usable at 390 px with no sideways scroll, WCAG AA contrast (the console's lowest
  text contrast is 6.4:1), labels on every control, a page title, visible keyboard focus.

## Decisions in this component

- **All API traffic goes through the Next server.** Server components call `apiJson()`. Client components call
  `/api/v1/...` or `/api/portal/...`, which forwards only `GET`, `POST`, and `PATCH` to `/v1/*` and `/portal/*`.
  `/internal/*`, `/dev/*`, and the docs are refused. Browser `X-Dev-*`, `Authorization`, `Cookie`,
  `X-Forwarded-*`, and `Host` headers are dropped; the server attaches the identity. Every request gets an
  `x-request-id`, which the API uses as its `correlation_id`.
- **Lenient responses, strict requests.** Generated response schemas keep unknown fields and accept unknown enum
  values (CHANGES.md item 5). Schemas used only in request bodies are strict and keep the contract's limits.
- **Clerk sign-in (RECALLOPS_WEB_AUTH=clerk).** `src/proxy.ts` runs `clerkMiddleware` on everything except
  `/claim/*`, `/api/portal/*`, and static files, and redirects a visitor without a session from `/app/*` and
  `/organization` to `/sign-in`. Console API calls go through `/api/v1/*`, where `auth-seam.ts` reads the session on
  the server and sends `Authorization: Bearer <session token>`; without a session the route answers 401 and never calls
  the API, and the browser's own `Authorization` and `X-Dev-*` headers are always dropped. The console layout sends a
  user with no organization to `/organization` (create or select one) and stops a role other than `org:admin` or
  `org:reviewer` before any data loads, mirroring the backend's 403s. The organization switcher and user menu sit in
  the shell header, and the console's query cache is keyed by organization. `ClerkProvider` wraps only the console,
  sign-in, sign-up, and organization pages, so the claimant portal never loads Clerk. Keyless mode and telemetry are
  off. The token never reaches browser JavaScript: every console call runs on the Next server.
- **Dev auth fails closed.** The console gets an identity only with `RECALLOPS_WEB_AUTH=dev`, a UUID firm id, and an
  `admin` or `reviewer` role, and never when `VERCEL_ENV=production`. None of the settings is `NEXT_PUBLIC_`.
- **Packages beyond the CLAUDE.md pin list**, pinned exactly because the pinned stack cannot build or test without
  them: `@tailwindcss/postcss` 4.3.3 (Tailwind 4's PostCSS plugin), `@types/react` and `@types/react-dom` 19.3.0,
  `@types/node` 22.20.5 (matches Node 22), `vite` 8.3.4 (vitest 5's peer). Not installed yet: Stripe and Supabase
  (no browser use in the current flows) and Playwright.
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
- **Program lifecycle.** `/app/programs/new` (admin) searches `GET /v1/recalls` or takes a notice typed in, and
  `POST /v1/programs` returns a draft with extracted rules (the regex extractor first; the model only as a fallback,
  which replay mode answers at no cost). `/app/programs/[id]/settings` edits the rules and the review threshold
  (`PATCH` replaces each rules object whole, so evidence, remedy options, and unknown keys are carried over), shows
  each value's notice quote with a verbatim mark, and moves the program draft to open to closed, each confirmed
  inline. Closing cannot be undone; a 402 on opening links to Billing. Reviewers see the page read-only.
- **Payouts** (`/app/payouts`, admin; hidden from reviewers in the sidebar): export batch history, a confirmed
  "Create export batch" (it marks payouts exported and claims paid; `null` reads as "nothing to export"), and CSV
  downloads through `/api`, whose sha256 matches the listed fingerprint.
- **Claim links.** Claim detail offers admins a confirmed reissue (`POST /v1/claims/{id}/link`); the new link is shown
  once, held only in memory, and never put in a URL or storage. Paid and closed claims explain why they cannot.
- **Fraud rings** (`/app/programs/[id]/fraud`): rings from `GET .../fraud-graph?limit=500`, largest first, with a small
  diagram whose line style (not colour) names what the claims share, filters by kind and by open review, and a 503
  that says duplicate checks still protect every decision.
- **Owner notices** (`/app/programs/[id]/campaign`, admin; read-only for reviewers): synthetic registration CSV upload
  (checked for size and type first; rejected rows listed with reasons; a template download), match, draft by channel,
  and a confirmed simulated send. Sample notices show a marker where `{claim_link}` stands, never a link.
- **Keyboard.** In the queue, `j` and `k` move and `Enter` opens the selected claim; on a claim, `a`, `d`, and `i` open
  the approve, deny, and request-info confirmation and `Esc` closes it. Keys are ignored while typing or with a
  modifier, and nothing is sent without Confirm.
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
- The browser half of Clerk is built in apps/web by the UI owner (2026-10-21), against the backend half from PR #8.
  `.github/CODEOWNERS` still routes `/apps/web/src/middleware.ts`, `src/lib/auth/`, and the sign-in and sign-up pages
  to the login owner; Next 16 uses `src/proxy.ts` instead of `middleware.ts`, so that entry needs updating (a
  backend-owned file).

**Payments owner and whoever runs the backend (component 3, operations)**
- Point the Connect return and refresh settings at the portal: `CONNECT_RETURN_URL=http://localhost:3000/claim/connect-return`
  and `CONNECT_REFRESH_URL=http://localhost:3000/claim/connect-refresh` (production: the web origin). They are one
  fixed URL for every claim, so before leaving for Stripe the tab stores the claim token in `sessionStorage`; the
  landing page reads it once, deletes it, and opens that claim's status page, which re-reads the API. Only an
  `https` URL on `stripe.com` is followed. Without these settings Stripe returns the claimant to `/dev/console`.
- Point `BILLING_RETURN_URL` at the console's billing page: `http://localhost:3000/app/billing?stripe=return`
  (production: the web origin). The page polls usage for 60 s after a Stripe return.

**Backend owner (component 1)**
- `reissue_link` writes its `claim_link_reissued` audit row with `target_type="program"` and the claim's id as
  `target_id` (`programs._audit` hardcodes the type), so the claim's own audit trail never shows a reissue.
- A program created from a typed notice is titled "Manual recall" in every view: `ManualRecall.title` is not kept on
  the program. The new-program page says so.
- 02-ui.md's `/app/settings` asks for a firm-wide default threshold and a notification email; the contract has no
  route for either, so the console offers per-program thresholds and Clerk's organization page only.
- A local API started before PR 8 was merged lacks `POST /v1/claims/{id}/link` (32 of 33 routes); restart it after
  pulling. No migration is needed for that route.
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
- Clerk pins: `@clerk/nextjs` 7.9.17 (CLAUDE.md) resolved `@clerk/backend` 3.26.0, `@clerk/shared` 4.41.0, and
  `@clerk/react` 6.18.0, all published 2026-10-20. package.json `overrides` hold them at 3.25.0 (the CLAUDE.md pin),
  4.40.0, and 6.17.8, the releases current when 7.9.17 shipped.
- The packages listed above under "beyond the pin list" need a decisions-log entry if the pin list is meant to cover
  apps/web devDependencies.
