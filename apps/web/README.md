# apps/web: RecallOps console and claim portal (component 2)

Next.js 16 App Router, React 19, TypeScript strict, Tailwind 4. Talks only to the RecallOps API (component 1),
always from the server: the API has no CORS, and identity is attached server-side. Spec: docs/components/02-ui.md;
onboarding: docs/onboarding/ui.md.

Status: **every console screen and the claimant portal are built** on the API contract, and the site is live at
https://recallops-five.vercel.app (2026-10-21).

- Homepage (`/`, public, no Clerk script): what RecallOps does, built on the real notice of CPSC recall 27024 with each
  phrase keyed to the rule taken from it, the steps of a recall, the measured results, and what is synthetic.
- Console (`/app`, dev auth or Clerk): products with a portfolio summary (`/app/programs`), the product overview with
  its one claim link, QR code, and printable poster (`/app/programs/[id]`), claims queue with filters, keyset paging, polling, and `j`/`k` keys (`/app/programs/[id]/claims`), rules editor,
  review threshold, and lifecycle (`/app/programs/[id]/settings`), monthly progress reports with the PDF
  (`/app/programs/[id]/reports`), fraud rings (`/app/programs/[id]/fraud`), owner notice campaigns
  (`/app/programs/[id]/campaign`), claim detail with reviewer actions and link reissue (`/app/claims/[id]`), the
  agent trace (`/app/claims/[id]/trace`), payout export batches (`/app/payouts`), and billing and usage with the
  Stripe return page (`/app/billing`, `/app/billing/return`).
- Sign-in (Clerk mode): `/sign-in` and `/sign-up`. One account is one company (plan v3, 2026-10-22): no
  organizations (`/organization` redirects to `/app`). A new company names itself and picks Free or Growth at
  `/app/welcome` (prices from `GET /v1/plans`), then asks for a recall at Recall access (`/app/access`).
- Company verification: until the platform operator approves a recall, a company reaches only Recall access, Billing,
  and its name. The operator (recognized by the API, with no company) works in one standalone window, `/operator`:
  pending requests with Approve and Reject, decided ones folded with Revoke. Approving opens the company's product.
- One claim link per product: the product page shows the link, its QR code (SVG and PNG downloads), a one-page
  printable poster, and "Replace the link", which stops the old one. A claim's own link can still be reissued.
- Public product link (no Clerk, never indexed): `/file/[token]` shows the product's facts and starts a claim, which
  opens the claimant's private link.
- Claimant portal (public, no Clerk): `/claim/[token]`, `/claim/[token]/status`, `/claim/connect-return`, and
  `/claim/connect-refresh`.

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
src/proxy.ts                 Next 16 proxy: clerkMiddleware on the console in Clerk mode; never on /claim, /file, or
                             /api/portal

src/app/layout.tsx           root layout (no Clerk: ClerkShell provides it per section)
src/app/page.tsx             public homepage; its content is src/lib/home-content.ts
src/app/app/                 console: layout (routing on GET /v1/me, the approval gate, AppShell, react-query
                             Providers keyed by account), error boundary, and the welcome (company setup: name and
                             plan, Onboarding), programs (products), programs/new (redirects to Recall access),
                             programs/[id] (overview with the claim link, claims, settings, reports, fraud,
                             campaign), claims/[id] (detail, trace), access, payouts, and billing (with return) pages
src/app/claim/               claimant portal: [token] (stepper), [token]/status, connect-return, connect-refresh
src/app/file/                public product claim link: [token] (product facts, Start my claim); no Clerk, never indexed
src/app/sign-in/ sign-up/    Clerk sign-in and sign-up (Clerk mode)
src/app/organization/        redirects to /app (plan v3 has no organizations)
src/app/operator/            the platform operator's standalone window (no console sidebar)
src/app/not-found.tsx        the branded 404 for public pages; src/app/app/not-found.tsx (with the [...missing] catch-all)
                             keeps a mistyped console URL inside the shell
src/app/icon.svg             the shield mark as the tab icon (dark-mode aware); apple-icon.png (180x180) for home screens
src/app/api/[...path]/       same-origin proxy for client components: /api/v1/* and /api/portal/* only

src/lib/api-types.ts         GENERATED zod schemas and types; never edit by hand
src/lib/api/config.ts        server settings (RECALLOPS_API_URL, timeout)
src/lib/api/auth-seam.ts     console identity: the Clerk session token (clerk) or the dev headers (dev); else fail closed
src/lib/api/forward.ts       proxy rules: path allowlist, header allowlists (unit-tested)
src/lib/api/backend.ts       server-side API calls with timeouts and typed errors; apiJson() parses with zod
src/lib/api/problem.ts       RFC 7807 -> { status, title, detail, correlationId, retryAfterSeconds } + human sentence
src/lib/api/queries.ts       page loaders returning { ok, data } or { ok: false, problem } (pages render the error state)
src/lib/api/operator.ts      the operator list loaded once per request
src/lib/api/client.ts        browser fetch through /api, validated with zod
src/lib/auth/                Clerk mode rules (routes, matcher, console access), the server-side session lookup, the
                             current console role, and the dev-mode shell identity
src/lib/queries/             react-query options (queue, claim, and trace polling)
src/lib/status.ts            labels for claim, run, payout, outcome, and program statuses; unknown -> "In progress"
src/lib/format.ts            counts, cents, UTC dates and times, percents
src/lib/programs.ts          figures derived from Program fields only (filed, awaiting review, corrected)
src/lib/products.ts          product page rules: where the claim link stands, the poster's facts, the rules note memory
src/lib/program-create.ts    recall search for Recall access
src/lib/program-rules.ts     rules editor model, remedy options that follow the remedy, lifecycle moves, close impact
src/lib/claims.ts            queue URL state, API path, review classification
src/lib/claim-detail.ts      reviewer action rules (mirrors claims/review.py), note limits, photo readings and duplicate
                             hits from the trace, citations, cap use, polling and worker waits
src/lib/claim-link.ts        claim link reissue rules
src/lib/qr.ts                QR codes for claim links (qrcode-generator): module matrix, SVG path, PNG rectangles, and
                             the product's one-page poster
src/lib/shortcuts.ts         console keyboard shortcuts (queue and claim)
src/lib/mpr.ts               monthly progress report periods, boxes, and sources
src/lib/fraud.ts             fraud graph to rings
src/lib/campaign.ts          owner notice campaigns: registration CSV checks, matching, drafts, simulated send
src/lib/payouts.ts           export batch downloads, file names, authors, totals
src/lib/billing.ts           billing page rules over GET /v1/usage
src/lib/billing-actions.ts   Stripe Checkout and Customer Portal handoff
src/lib/stripe-url.ts        only Stripe-hosted URLs are followed
src/lib/portal.ts            portal steps, validation, resumable drafts, plain status, payout choice and deadline,
                             Stripe handoff, the device's memory of claims it started (48 hours) and forgetting one
src/lib/portal-copy.ts       plain sentences for portal errors
src/lib/intake.ts            product claim links: token check and the server-side read of /portal/intake/{token}
src/lib/image.ts             on-device photo compression (under 1.5 MB, longest edge 2000 px)
src/lib/home-content.ts      homepage copy: the recall 27024 quote (tested word for word), rules, steps, results
src/lib/clerk-appearance.ts  dark-mode palette for Clerk's user menu (Clerk's own palette is light)
src/lib/access.ts            company verification: request bodies, status words, the operator's actions, outcomes,
                             and domain hint
src/lib/me.ts                who is signed in (GET /v1/me), the plans (GET /v1/plans), and where an account belongs
src/lib/focus.ts             focus for inline confirmations when they open

src/components/              shell and shared: AppShell, NavLinks, BrandMark, AccountControls, ClerkShell,
                             ClerkConsole, AuthOff, StatusBadge, ProblemAlert, States (LoadingState, EmptyState)
                             products: ProgramsTable, ProgramPortfolio, ProgramOverview (ProductBanner),
                             ProgramSettings, ProductLinkCard (ReplaceLinkConfirm), RulesCheckNote
                             (RulesCheckReminder), QrCode (QrPanel, the print window)
                             claims: ClaimsQueue (client), ClaimsTable, QueueControls, ClaimDetailLive (client),
                             ClaimPanels, ReviewerActions (client), ReissueLink (ReissuedLink), TraceView, TraceLive
                             reports, fraud, campaign: MprGenerate, MprReportView, FraudRings, Campaign
                             money: PayoutExports, BillingScreen, BillingView, BillingLive
                             homepage (home/): NoticeFigure
                             company verification: AccessGate (routeConsole, gateConsole), AccessRequests,
                             OperatorQueue, ClerkOperatorWindow (in ClerkConsole), OperatorFrame (in ClerkShell)
src/components/portal/       ClaimStepper, PhotoCapture, StatusView, NeedsInfoReply, PayoutChoice, AutoRefresh,
                             ConnectReturn, StartClaim (StartClaimActions, RememberedClaim), ClaimLinkCard,
                             RecallFacts (ClaimHeader, RecallContact, LinkNotActive), PortalFrame
tests/                       vitest (node environment; components rendered with react-dom/server)
```

## Design system (baseline for every screen)

Restyled at merge (2026-10-21) to match the README graphics in `docs/assets`, at the backend owner's request; the
layouts, components, and accessibility rules come from the UI owner's Ink & Paper system. New screens use these
tokens and classes; do not add one-off colors or fonts.

- **Tokens** live only in `src/app/globals.css`: a white page and surfaces, panel `#F6F6F3` (sidebar, table headers),
  ink `#161616`, secondary ink `#52514E`, warm border `#E1E0D9`, input border `#8A8881` (3.5:1), and one blue,
  `brand` and `accent`: `#2672CF` in light mode (4.8:1 on white, so it can be text and sits behind white button text)
  and the README's `#3987E5` in dark mode. The README's `#2A78D6` is 4.4:1, just under AA for body text.
- **Blue** marks actions, selection, progress, link underlines, and focus. It is never a status.
- **Status colors are separate from the brand:** green for approved, completed, paid; amber for flagged, pending
  review, warnings; red for denied, errors, destructive actions (`success`, `warn`, `danger` tokens and
  `StatusBadge` tones).
- **Type:** the system sans everywhere (the README graphics' stack), so no font is downloaded. Hierarchy comes from
  size and weight: `page-title` (30 px, 26 px on phones), `display-title` (portal recall titles, status headlines,
  empty states), `section-title` (18 px), `kpi-value` (28 px), `eyebrow`.
- **Brand mark:** `components/BrandMark.tsx`, the shield and check from the README banner, used by the sidebar
  wordmark and the portal header.
- **Components:** cards are `rounded-lg border border-line bg-surface`; primary buttons solid blue (`bg-accent
  text-bg`), secondary buttons white with `border-line-strong`; destructive actions red outline. No gradients, glass,
  heavy shadows, neon, or decorative oversize elements.
- **Checks for any new screen:** usable at 390 px with no sideways scroll, WCAG AA contrast, labels on every control,
  a page title, visible keyboard focus. Dark mode follows the system and uses the README's dark values.

## Decisions in this component

- **All API traffic goes through the Next server.** Server components call `apiJson()`. Client components call
  `/api/v1/...` or `/api/portal/...`, which forwards only `GET`, `POST`, and `PATCH` to `/v1/*` and `/portal/*`.
  `/internal/*`, `/dev/*`, and the docs are refused. Browser `X-Dev-*`, `Authorization`, `Cookie`,
  `X-Forwarded-*`, and `Host` headers are dropped; the server attaches the identity. Every request gets an
  `x-request-id`, which the API uses as its `correlation_id`.
- **Lenient responses, strict requests.** Generated response schemas keep unknown fields and accept unknown enum
  values (CHANGES.md item 5). Schemas used only in request bodies are strict and keep the contract's limits.
- **Clerk sign-in (RECALLOPS_WEB_AUTH=clerk).** `src/proxy.ts` runs `clerkMiddleware` on everything except the
  homepage, `/claim/*`, `/file/*`, `/api/portal/*`, and static files, and redirects a visitor without a session from
  `/app/*` and `/operator` to `/sign-in`. Console API calls go through `/api/v1/*`, where `auth-seam.ts` reads the
  session on the server and sends `Authorization: Bearer <session token>`; without a session the route answers 401
  and never calls the API, and the browser's own `Authorization` and `X-Dev-*` headers are always dropped. One account
  is one company (plan v3), so any signed-in company account is an admin and there is no organization switcher. The
  console layout routes on `GET /v1/me`: the operator goes to `/operator`, a company not set up sees its setup page in
  place (the Stripe return page still renders), a 503 shows "Sign-in verification is unavailable", and an unapproved
  company reaches only Recall access and Billing. The query cache is keyed by the account's user id. `ClerkProvider`
  wraps only the console, sign-in, sign-up, and operator pages, so neither a claimant nor a visitor to a product link
  loads Clerk. Keyless mode and telemetry are off. The token never reaches browser JavaScript: every console call runs
  on the Next server.
- **Dev auth fails closed.** The console gets an identity only with `RECALLOPS_WEB_AUTH=dev`, a UUID firm id, and an
  `admin` or `reviewer` role, and never when `VERCEL_ENV=production`. None of the settings is `NEXT_PUBLIC_`.
- **Packages beyond the CLAUDE.md pin list**, pinned exactly because the pinned stack cannot build or test without
  them: `@tailwindcss/postcss` 4.3.3 (Tailwind 4's PostCSS plugin), `@types/react` and `@types/react-dom` 19.3.0,
  `@types/node` 22.20.5 (matches Node 22), `vite` 8.3.4 (vitest 5's peer). `qrcode-generator` 2.0.4 (MIT, no
  dependencies, released 2025-08-07) draws claim-link QR codes (owner decision 2026-10-22). Not installed yet: Stripe and Supabase
  (no browser use in the current flows) and Playwright.
- **Toolchain:** the lockfile was written by npm 10.9.4 (the pin), and CI uses Node 22.22.0. Local verification ran on Node 24.15.0.

- **Program figures come from the contract only.** "Claims filed" is every claim past `draft` (a claim started
  from the product link but not submitted); "Awaiting review" is `flagged + under_review + capped`; "Corrected" is `paid + closed` as a
  share of `units_affected`, labelled as indicative because the MPR is the authoritative count. Next MPR due,
  model spend per program, and charts are out of the MVP (no endpoint).
- **Claims queue.** The server renders the first page (it works without JavaScript); in the browser, react-query
  polls the page and the program's counts every 10 s through `/api`, skips ticks while the tab is hidden, refreshes
  as soon as it is shown, and keeps the last good page on screen when a poll fails (no retry bursts; the next tick
  retries). Filters and pages are URL state (`?status=&cursor=`); a filter change returns to page one. Claims started from the product link but not submitted (`draft`) appear only
  under "Started, not submitted". The cursor is
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
- **Product lifecycle.** The console no longer creates products: the operator's approval of a recall request creates
  the product from the notice (the regex extractor first) and opens it, and `/app/programs/new` redirects to Recall
  access. An approved request with no product offers "Create the product". `/app/programs/[id]/settings` edits the
  rules and the review threshold (`PATCH` replaces each rules object whole, so evidence and unknown keys are carried
  over; remedy options are rebuilt to match a changed remedy, keeping every proof the options asked for), shows each
  value's notice quote with a verbatim mark, and moves the product between draft, open, and closed, each confirmed
  inline. Closing cannot be undone; a refund product with no price stays draft until the price range is set; a 402 on
  opening says Free covers one product and links to Billing.
- **Payouts** (`/app/payouts`, admin; hidden from reviewers in the sidebar): export batch history, a confirmed
  "Create export batch" (it marks payouts exported and claims paid; `null` reads as "nothing to export"), and CSV
  downloads through `/api`, whose sha256 matches the listed fingerprint.
- **Claim links.** Each product has one public link (`Program.intake_url`), shown with its QR code and a printable
  poster; "Replace the link" stops the old link and QR at once. A claim's own private link can be reissued from claim
  detail (`POST /v1/claims/{id}/link`), to be sent only to the email on that claim; the new link is shown once, held
  only in memory, and never put in a URL or storage. Paid and closed claims explain why they cannot.
- **Fraud rings** (`/app/programs/[id]/fraud`): rings from `GET .../fraud-graph?limit=500`, largest first, with a small
  diagram whose line style (not colour) names what the claims share, filters by kind and by open review, and a 503
  that says duplicate checks still protect every decision.
- **Owner notices** (`/app/programs/[id]/campaign`, admin; read-only for reviewers): synthetic registration CSV upload
  (checked for size and type first; rejected rows listed with reasons; a template download), match, draft by channel,
  and a confirmed simulated send. Sample notices show the product's link where `{claim_link}` stands, or a marker
  when the product is not taking claims.
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
  Stripe onboarding) appears on every plan when the claim is `approved` and the payout is `pending`, `onboarding`,
  `failed`, or `pending_export`, with the day an unfinished choice becomes a mailed check (`payout_choice_due`);
  otherwise the page shows progress. Submit waits for the label photo. Each claim page shows a "Your private claim
  link" card (Copy, Share, a QR code to continue on another device, and "Forget this claim on this device"). For 48
  hours the device remembers the claim it started from a product link, so scanning the link again offers "Continue
  your claim" (with Forget beside it); it keeps no other claim, so a Stripe return in another tab asks for the private
  link. A submitted claim's page refreshes every 15 s at most 8 times, inside the link's 60 requests per hour.

## Notes kept from the component owners

The owner builds every component alone since 2026-10-22; these notes stay because the settings they describe still
apply.

**Login owner (component 4)**
- The browser half of Clerk is built in apps/web by the UI owner (2026-10-21), against the backend half from PR #8.
  `.github/CODEOWNERS` routes `src/proxy.ts`, `src/lib/auth/`, and the sign-in and sign-up pages to the login owner
  (fixed at merge: it named `middleware.ts`).

**Payments owner and whoever runs the backend (component 3, operations)**
- Point the Connect return and refresh settings at the portal: `CONNECT_RETURN_URL=http://localhost:3000/claim/connect-return`
  and `CONNECT_REFRESH_URL=http://localhost:3000/claim/connect-refresh` (production: the web origin). They are one
  fixed URL for every claim, so before leaving for Stripe the tab stores the claim token in `sessionStorage`; the
  landing page reads it once, deletes it, and opens that claim's status page, which re-reads the API. Only an
  `https` URL on `stripe.com` is followed. Without these settings Stripe returns the claimant to `/dev/console`.
- `BILLING_RETURN_URL` defaults to `http://localhost:3000/app/billing/return` (production: the web origin). The API
  refuses query strings in return URLs, so the return is its own page; `?stripe=return` still works. The page polls
  usage every 3 s for 60 s. The Connect URLs above are now the API's defaults too (CHANGES item 47).

**Backend owner (component 1)**
- Closed at merge (CHANGES items 41 to 46): the reissue audit row is filed under the claim; a typed notice keeps its
  title; the API itself answers `under_review` to the claimant for `flagged` and `capped`; contract examples keep
  their nulls (`tests/api-types.test.ts` no longer tolerates gaps); the claims list declares its 400; `Decision`
  carries `hold_reason`, `reopened_from`, `duplicate_found_later`, `duplicates_ack`, and `question_for_claimant`, and
  claim detail shows why a claim waits and which claim reopened it.
- Still open: 02-ui.md's `/app/settings` (a firm-wide default threshold and a notification email) has no route, so
  the console offers per-product thresholds and the company name under Billing only.
- Clerk pins: `@clerk/nextjs` 7.9.17 (CLAUDE.md) resolved `@clerk/backend` 3.26.0, `@clerk/shared` 4.41.0, and
  `@clerk/react` 6.18.0, all published 2026-10-20. package.json `overrides` hold them at 3.25.0 (the CLAUDE.md pin),
  4.40.0, and 6.17.8, the releases current when 7.9.17 shipped.
- The web pins and the packages beyond the pin list are recorded in CLAUDE.md (Stack, 2026-10-21).
