# Going live: one link for the whole project

Nothing here has been run yet. RecallOps is deploy-ready, not deployed: every service, setting, and secret is listed below and in `infra/render.yaml`, and the code refuses to start in production with example, localhost, or plain-http values. Two of the hosted pieces were smoke-tested on 2026-10-19: Neo4j AuraDB passed 10 of 10 checks and the Supabase photo bucket passed 6 of 6. The rest is untested until the first deploy. Budget about two hours for the first run, and expect one or two settings to need a second try.

## What runs where

| Piece | Host | Plan | What it does |
|---|---|---|---|
| Website: console and claim portal (`apps/web`) | Vercel | Hobby (free) | **The one link people open.** The console at `/app`, claim links at `/claim/<token>` |
| API (`recallops-api`) | Render web service | Free | Every request from the website; Stripe webhooks; the nightly CPSC pull |
| Worker (`recallops-worker`) | Render background worker | Starter, about $7 a month | Decides each submitted claim with the AI agent |
| Payment jobs (`recallops-payment-jobs`) | Render cron, every 10 minutes | Paid by the minute | Reports usage to Stripe and sends Connect refunds |
| Check export (`recallops-check-export`) | Render cron, nightly | Paid by the minute | Batches mailed-check refunds (an admin can also press "Create export batch") |
| Database | Supabase Postgres 16 | Free | All data, with row-level security |
| Claim photos | Supabase Storage, private bucket `claim-photos` | Free | Photos; the API proxies every byte after the firm check |
| Fraud graph | Neo4j AuraDB Free | Free | Fraud rings (already created; credentials in `~/.config/recallops/deploy.env`) |
| Sign-in | Clerk | Free | Company staff and organizations |
| Payments | Stripe, test mode only | Free | Firm plans and claimant refunds; no real money |

The website is the only address anyone visits. The browser never talks to the API directly: the website's server calls it, attaches the signed-in user's Clerk token, and passes through only `/v1` and `/portal` requests. The worker and the cron jobs have no public address.

## Secrets to generate once

Run each line once and keep the output somewhere private (a password manager, or `~/.config/recallops/deploy.env`, mode 600). Never commit them.

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"                    # WEB_PROXY_SECRET (API) = RECALLOPS_WEB_PROXY_SECRET (website)
python3 -c "import secrets;print(secrets.token_urlsafe(32))"                    # INTERNAL_SHARED_SECRET (API and the GitHub feed job)
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"  # CLAIM_TOKEN_SECRET (signs claim links)
python3 -c "import secrets;print(secrets.token_urlsafe(48))"                    # GRAPH_KEY_SECRET, if not already in deploy.env
python3 -c "import secrets;print(secrets.token_urlsafe(24))"                    # one password per database role (three)
```

## 1. Database (Supabase)

1. In the Supabase project (already created), open **Project Settings, Database** and copy the direct connection string for the `postgres` user. Call it `SUPABASE_ADMIN_URL`.
2. Create the three roles and the `recallops` database, then run the migrations, from the repository root:

```bash
psql "$SUPABASE_ADMIN_URL" -v ON_ERROR_STOP=1 \
  -v owner_password="$OWNER_PW" -v app_password="$APP_PW" -v internal_password="$INTERNAL_PW" -v dbname=recallops \
  -f apps/api/sql/bootstrap_roles.sql
cd apps/api && PYTHONPATH=src uv run --frozen alembic \
  -x dburl="postgresql://recallops_owner:$OWNER_PW@<supabase-host>:5432/recallops" upgrade head
```

3. Build the three connection strings for Render: `recallops_app` becomes `DATABASE_URL` and `recallops_internal` becomes `DATABASE_URL_INTERNAL`. The owner role is used only for migrations, never by a running service.

Untested here: the script gives `recallops_internal` the BYPASSRLS attribute, and Postgres lets only a role that has BYPASSRLS itself grant it. If Supabase's `postgres` user refuses, run that one `ALTER ROLE` from the Supabase SQL editor, which runs with more privilege. The pgvector extension is called `vector` on Supabase too.

## 2. API, worker, and jobs (Render)

1. In Render choose **New, Blueprint**, and point it at this repository. It reads `infra/render.yaml` and proposes four services.
2. Fill in every value marked `sync: false`. The table below says where each one comes from. The website's address is not known yet, so the first deploy of the API will refuse to start: that is expected. Come back after step 3 and fill in the website values.

| Setting | Where it comes from |
|---|---|
| `DATABASE_URL`, `DATABASE_URL_INTERNAL` | Step 1 |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `GRAPH_KEY_SECRET` | `deploy.env` (this Aura instance names its user and database after the instance id) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | `deploy.env` (the legacy `service_role` key; the adapter is untested with the new `sb_secret_` keys) |
| `WEB_PROXY_SECRET`, `INTERNAL_SHARED_SECRET`, `CLAIM_TOKEN_SECRET` | Generated above |
| `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_ISSUER` | Step 4 (leave `CLERK_JWKS_URL` empty: it is derived from the issuer) |
| `CLERK_ALLOWED_ORIGINS` | `https://<your-site>.vercel.app` |
| `PORTAL_BASE_URL` (every service) | `https://<your-site>.vercel.app/claim` |
| `BILLING_RETURN_URL` | `https://<your-site>.vercel.app/app/billing/return` |
| `CONNECT_RETURN_URL`, `CONNECT_REFRESH_URL` | `https://<your-site>.vercel.app/claim/connect-return` and `.../claim/connect-refresh` |
| `STRIPE_*` | Step 5 (test-mode keys only; live keys are refused) |
| `ANTHROPIC_API_KEY` (worker only) | Your Anthropic key. The cost meter stops all model calls at the ledger's limit |

3. When the API is up, `https://<api>.onrender.com/healthz` answers with `"status":"ok"` and `"database":true`. `/readyz` answers 503 until the worker has sent a heartbeat, which is how you know the worker is running.

The free web service sleeps after 15 minutes without traffic, so the first click after a quiet spell takes about a minute. The worker cannot be free on Render. If you skip it, claims wait in "submitted" until a worker runs somewhere: a laptop running `ADJUDICATOR=agent LLM_MODE=live make worker` with the production `DATABASE_URL_INTERNAL` works for a demo.

## 3. Website (Vercel)

1. In Vercel choose **Add New, Project**, import this repository, and set **Root Directory** to `apps/web`. Vercel detects Next.js; Node 22 is the default.
2. Set these environment variables (Production):

| Variable | Value |
|---|---|
| `RECALLOPS_API_URL` | `https://<api>.onrender.com` (https is required for anything that is not this machine) |
| `RECALLOPS_WEB_AUTH` | `clerk` (the dev headers refuse to run on any Vercel deployment) |
| `RECALLOPS_WEB_PROXY_SECRET` | The same value as the API's `WEB_PROXY_SECRET` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Step 4 |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | `/sign-in`, `/sign-up` |
| `NEXT_PUBLIC_CLERK_KEYLESS_DISABLED`, `NEXT_PUBLIC_CLERK_TELEMETRY_DISABLED` | `true`, `true` |

3. Deploy. The address Vercel gives you (`https://<your-site>.vercel.app`) is the one link. Put it into the Render settings from step 2 and redeploy the API.

Why the proxy secret matters: every claim page reaches the API from Vercel's servers. The secret lets the website tell the API which claimant is asking, so the portal's limit of 300 requests an hour per address applies to each claimant rather than to everyone at once. The API refuses to start in production without it.

## 4. Sign-in (Clerk)

1. Use the Clerk application from `docs/onboarding/login.md`: email and password, an emailed code at sign-up, and Organizations enabled with the roles `org:admin` and `org:reviewer` (make `org:reviewer` the default).
2. Under **Domains** or **Allowed origins**, add `https://<your-site>.vercel.app`.
3. Copy the publishable key, the secret key, and the Frontend API URL (which is `CLERK_ISSUER`). A development instance is fine for the class; it shows a small "development mode" badge.

## 5. Payments (Stripe test mode)

1. Follow `docs/onboarding/payments-runtime.md` to create the test-mode prices (or run `make billing-bootstrap` with your approval) and set the account's API version to `2026-09-30.endive`.
2. In the Stripe dashboard, add two webhook destinations pointing at `https://<api>.onrender.com/internal/stripe/webhook`: one for Billing and transfer events (snapshot, API version `2026-09-30.endive`), whose signing secret is `STRIPE_WEBHOOK_SECRET`, and one for Accounts v2 thin events, whose secret is `STRIPE_CONNECT_WEBHOOK_SECRET`.

## 6. The nightly CPSC pull (GitHub Actions)

In the repository's **Settings, Secrets and variables, Actions**, add `RECALLOPS_API_URL` (the Render API address) and `INTERNAL_SHARED_SECRET`. The `feeds` workflow then pulls new recalls every night at 07:17 UTC; until both exist it skips with a notice.

## 7. Check it end to end

1. Open the website, sign up, and create an organization. Your first sign-in creates the company's private space.
2. **New program**: search the CPSC archive (for example "laminator"), create the program, review the rules, and open it.
3. **Issue claim links**, open one in a private window on a phone, and file a claim with three photos.
4. Within a minute the worker decides it. Open the claim in the console and read its trace.
5. Generate the monthly report and download its PDF.

If anything fails, Render's logs are JSON lines with an `event` field: start with `http_request`, `worker_claim_done`, and any `level: error`.

## Costs

The free tiers cover the website, the API, the database, photos, the graph, sign-in, and Stripe test mode. The paid pieces are the Render worker (about $7 a month) and the two cron jobs (billed by the minute, typically cents a month), plus AI calls of about 0.2 cents per claim, all metered against the project's budget. The whole project has spent $1.63 on AI so far.
