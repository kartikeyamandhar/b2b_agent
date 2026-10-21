# Onboarding: UI owner (component 2, apps/web)

You own the firm console (`/app/...`) and the claim portal (`/claim/...`) in apps/web: Next.js 16, React 19, TypeScript strict, Tailwind 4, zod types generated from the contract. Your PR 9 was merged on 2026-10-21 with the backend owner's review fixes. **docs/contracts/CHANGES.md items 41 to 52** list what changed during that review; read them first.

For billing screens, upgrade prompts, claimant onboarding, payout status, and export downloads, the
[payments handoff: UI owner](payments.md#ui-owner) still applies.

## What changed in your component at merge

- **Visual system.** At the backend owner's request, the console and portal were restyled to match the README graphics: a white ground, warm neutral grays, one blue (`brand`, #2672cf light and #3987e5 dark), the system sans for everything, and the shield mark from the README banner (`components/BrandMark.tsx`). The tokens still live only in `src/app/globals.css`; `copper` became `brand`, and `font-serif` headings use the `display-title` utility. Your layouts, components, and accessibility rules (390 px, AA contrast, labels, focus) are unchanged. The two Fontsource packages were removed.
- **Security fixes** from the review (CHANGES item 49): portal calls name the claimant's address to the API with a shared secret (`RECALLOPS_WEB_PROXY_SECRET`); dev auth answers only on localhost and only against a local API; `RECALLOPS_API_URL` must be https unless it is this machine; `next dev` binds to 127.0.0.1; frame, sniffing, referrer, and robots headers are set in `next.config.ts`.
- **UI defects fixed** from the review: see CHANGES item 50 for the list (payout choice, reply photo race, upload timeout, rules editor remedy, close warnings, queued reviews, MPR periods, and more), each with a test.
- **Backend gaps you reported are closed** (CHANGES items 41 to 47): the claimant never sees `flagged` or `capped`; denial reasons name only failed rules; `decision` now carries `hold_reason`, `reopened_from`, `duplicate_found_later`, `duplicates_ack`, and `question_for_claimant` (claim detail shows the first three); a typed notice keeps its title; the reissue audit row is filed under the claim; the claims list declares its 400; contract examples keep their nulls (your types test no longer tolerates gaps).
- **Stripe returns** now default to your pages: `BILLING_RETURN_URL=http://localhost:3000/app/billing/return` (a new page, because the API refuses query strings in return URLs; `?stripe=return` still works) and the two Connect URLs at `/claim/connect-return` and `/claim/connect-refresh`.

## Run everything locally

Prerequisites: Docker, uv 0.11.21, Python 3.13, Node 22, make. A fresh machine pulls `pgvector/pgvector:0.8.6-pg16` and `neo4j:2026.09.0-community` once.

```
cp .env.example .env
docker compose up -d                 # Postgres and Neo4j
make setup                           # pinned deps for the API and the web app, roles, databases, migrations
make seed                            # the laminator recall and 500 decided claims, about 50 s
make dev                             # the API on http://127.0.0.1:8000
make web                             # in a second terminal: http://localhost:3000/app/programs
ADJUDICATOR=rules_only make worker   # in a third terminal: decides portal claims, free and offline
```

`make setup` creates `apps/web/.env.local` from `apps/web/.env.example` once (dev auth, the demo firm, admin role). Change `RECALLOPS_DEV_ROLE=reviewer` there to see the reviewer's console.

Stable ids across reseeds: demo firm `00000000-0000-4000-8000-00000000d001`, other firm `...d002`, demo program `0874f66f-bfb5-552b-bd93-6266f49c1d5a`, first claim `66dcc492-086f-5b59-b441-cbe001eb2e0b`. Development claim links are `/claim/dev.<firm id>.<claim id>`.

## Tests that must stay green

CI's `web` job runs `npm ci`, `tsc --noEmit`, `vitest run`, the generated-types check against `docs/contracts/openapi.json`, and `npm run build` (the build catches page exports and server and client boundary mistakes that tsc does not). `make web-check` runs the first three locally. The backend job includes `test_committed_openapi_matches_the_app`, so a contract change always arrives with regenerated types.

## Open items for the website

- Component tests render static markup; no test drives a click or an effect in a real DOM (jsdom). The review fixes were tested through pure functions and rendered states.
- Firm-wide settings (02-ui.md `/app/settings`: a default threshold and a notification email) have no API route; the console offers per-program thresholds and Clerk's organization page.
- Playwright end-to-end tests are not written.
- The site is live at https://recallops-five.vercel.app (Vercel project `recallops`, root directory `apps/web`): every push to `main` deploys it and every pull request gets a preview. The public homepage at `/` was added at deploy time (`src/app/page.tsx`, content in `src/lib/home-content.ts`); `docs/DEPLOY.md` covers the rest.
