# Going live: one link for the whole project

RecallOps has been live since 2026-10-21. The one link is **https://recallops-five.vercel.app**; the API answers at https://recallops-api-750b.onrender.com (`/healthz`, `/readyz`). This page is how it was deployed and how to deploy it again. The steps below are the ones that worked; [What the first deploy found](#what-the-first-deploy-found) lists where the first attempt differed from the plan. The code refuses to start in production with example, localhost, or plain-http values, so a missing setting stops a service at startup rather than running it half-configured.

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
| Sign-in | Clerk | Free | Company accounts (one account is one company) and the operator |
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

The project runs Postgres 17. Its direct host (`db.<ref>.supabase.co`) answers on IPv6 only, and neither Render nor most home networks reach it, so every connection goes through Supabase's **session pooler** on port 5432 (for this project `aws-0-us-east-1.pooler.supabase.com`; the other regional host answers "tenant not found"). Through the pooler a role logs in as `<role>.<project ref>`, and the pooler honours the database name, so the app keeps its own `recallops` database, out of reach of Supabase's public Data API.

1. In the Supabase SQL editor (it runs as `postgres`: no superuser, but CREATEROLE, CREATEDB, and BYPASSRLS), create the three roles with the attributes in `apps/api/sql/bootstrap_roles.sql`. The first deploy passed each password as a SCRAM-SHA-256 verifier computed locally, so no plaintext password went through the editor. Granting BYPASSRLS to `recallops_internal` works there.
2. Still in the editor: `GRANT recallops_owner TO postgres WITH INHERIT FALSE, SET TRUE;` (Postgres requires it before `postgres` may create a database owned by another role), then `CREATE DATABASE recallops OWNER recallops_owner` on its own (it cannot run inside a transaction).
3. As `recallops_owner` through the pooler, run the rest of the bootstrap inside the new database: revoke it from PUBLIC, grant CONNECT to the three roles, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, `GRANT USAGE ON SCHEMA public TO recallops_app, recallops_internal`, and `CREATE EXTENSION vector`. Supabase lets a database owner create `vector` (it is a privileged extension there), so the `postgres` password is never needed. Grants made by `postgres` on the database have no effect, because it holds the owner role without inheriting it.
4. Run the migrations as the owner: `cd apps/api && DATABASE_URL_OWNER='postgresql://recallops_owner.<ref>:<password>@<pooler-host>:5432/recallops?sslmode=require' PYTHONPATH=src uv run --frozen alembic upgrade head`. Expect head `0007_login`, 29 tables plus the 4 checkpointer tables, and row-level security forced on the 22 tenant tables.
5. Build the connection strings for Render the same way: `recallops_app` becomes `DATABASE_URL` and `recallops_internal` becomes `DATABASE_URL_INTERNAL`. The owner role is used only for migrations, never by a running service.

## 2. API, worker, and jobs (Render)

1. Add a payment card in Render (**Billing**) first: the worker and the two cron jobs are paid, and Render refuses to create them without one (HTTP 402).
2. In Render choose **New, Blueprint**, and point it at this repository. It reads `infra/render.yaml` and proposes four services in Virginia. The first deploy created the same four services through Render's API from the same file instead, with each secret read from the owner's machine; either way the services match the blueprint.
3. Fill in every value marked `sync: false`. The table below says where each one comes from. Create the Vercel project first (step 3 below, without deploying) so its address is known; otherwise the API refuses to start until the website values are filled in.
4. Auto-deploy on push needs Render's GitHub app to have access to this repository. Services created from the public repository URL build it, but a push to `main` does not redeploy them: deploy by hand (Render's **Manual Deploy**, or `POST /v1/services/<id>/deploys`) until the app is granted access.

| Setting | Where it comes from |
|---|---|
| `DATABASE_URL`, `DATABASE_URL_INTERNAL` | Step 1 |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `GRAPH_KEY_SECRET` | `deploy.env` (this Aura instance names its user and database after the instance id) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | `deploy.env` (the legacy `service_role` key; the adapter is untested with the new `sb_secret_` keys) |
| `WEB_PROXY_SECRET`, `INTERNAL_SHARED_SECRET`, `CLAIM_TOKEN_SECRET` | Generated above |
| `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_ISSUER` | Step 4 (leave `CLERK_JWKS_URL` empty: it is derived from the issuer). The API also reads operator addresses with the secret key |
| `CLERK_ALLOWED_ORIGINS` | `https://<your-site>.vercel.app` |
| `PLATFORM_OPERATOR_EMAILS` | The operator's email address: who approves companies (step 7) |
| `PLATFORM_OPERATOR_USER_IDS` | The operator's Clerk user id as well, once the account exists: checked locally, with no Clerk lookup (step 7) |
| `PORTAL_BASE_URL` (every service) | `https://<your-site>.vercel.app/claim` |
| `BILLING_RETURN_URL` | `https://<your-site>.vercel.app/app/billing/return` |
| `CONNECT_RETURN_URL`, `CONNECT_REFRESH_URL` | `https://<your-site>.vercel.app/claim/connect-return` and `.../claim/connect-refresh` |
| `STRIPE_*` | Step 5 (test-mode keys only; live keys are refused) |
| `ANTHROPIC_API_KEY` (worker only) | Your Anthropic key. The cost meter stops all model calls at the ledger's limit |

3. When the API is up, `https://<api>.onrender.com/healthz` answers with `"status":"ok"`. It is a cheap liveness check that never contacts the graph, so its `"graph":"disabled"` means "not checked here". `/readyz` answers 503 until the worker has sent a heartbeat (which is how you know the worker is running), and then reports the graph's real status.

The free web service sleeps after 15 minutes without traffic, so the first click after a quiet spell takes about a minute. The worker cannot be free on Render. If you skip it, claims wait in "submitted" until a worker runs somewhere: a laptop running `ADJUDICATOR=agent LLM_MODE=live make worker` with the production `DATABASE_URL_INTERNAL` works for a demo.

## 3. Website (Vercel)

1. Give Vercel's GitHub app access to this repository (GitHub **Settings, Applications, Vercel, Configure**); Vercel cannot link the project without it. Then choose **Add New, Project**, import this repository, set **Root Directory** to `apps/web` and Node to 22.x. With the link in place, every push to `main` deploys the site and every pull request gets a preview.
2. Set these environment variables (Production):

| Variable | Value |
|---|---|
| `RECALLOPS_API_URL` | `https://<api>.onrender.com` (https is required for anything that is not this machine) |
| `RECALLOPS_WEB_AUTH` | `clerk` (the dev headers refuse to run on any Vercel deployment) |
| `RECALLOPS_WEB_PROXY_SECRET` | The same value as the API's `WEB_PROXY_SECRET` |
| `RECALLOPS_API_TIMEOUT_MS` | `55000`: the free API sleeps after 15 idle minutes and takes about a minute to wake |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Step 4 |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | `/sign-in`, `/sign-up` |
| `NEXT_PUBLIC_CLERK_KEYLESS_DISABLED`, `NEXT_PUBLIC_CLERK_TELEMETRY_DISABLED` | `true`, `true` |

3. Deploy. The address Vercel gives you (`https://<your-site>.vercel.app`) is the one link. Put it into the Render settings from step 2 and redeploy the API.

Why the proxy secret matters: every claim page reaches the API from Vercel's servers. The secret lets the website tell the API which claimant is asking, so the portal's limit of 300 requests an hour per address applies to each claimant rather than to everyone at once. The API refuses to start in production without it.

## 4. Sign-in (Clerk)

1. Use a Clerk application with email and password and an emailed code at sign-up. Leave Organizations off: since plan v3 (2026-10-22) one account is one company, and the API ignores organization claims. Turn test mode off in the Clerk dashboard if you can, so `+clerk_test` addresses cannot be verified with a fixed code; either way the operator's queue marks a test address next to the company's email. The live instance requires passwords of at least 15 characters.
2. Under **Domains** or **Allowed origins**, add `https://<your-site>.vercel.app`.
3. Copy the publishable key, the secret key, and the Frontend API URL (which is `CLERK_ISSUER`). A development instance is fine for the class; it shows a small "development mode" badge.

## 5. Payments (Stripe test mode)

1. Follow `docs/onboarding/payments-runtime.md` to create the test-mode prices (or run `make billing-bootstrap` with your approval) and set the account's API version to `2026-09-30.endive`.
2. In the Stripe dashboard, add two webhook destinations pointing at `https://<api>.onrender.com/internal/stripe/webhook`: one for Billing and transfer events (snapshot, API version `2026-09-30.endive`), whose signing secret is `STRIPE_WEBHOOK_SECRET`, and one for Accounts v2 thin events, whose secret is `STRIPE_CONNECT_WEBHOOK_SECRET`.

## 6. The nightly CPSC pull (GitHub Actions)

In the repository's **Settings, Secrets and variables, Actions**, add `RECALLOPS_API_URL` (the Render API address) and `INTERNAL_SHARED_SECRET`. The `feeds` workflow then pulls new recalls every night at 07:17 UTC; until both exist it skips with a notice.

## 7. Company verification: you are the operator

A company runs a recall only after the platform operator approves it. Put the operator's email address in `PLATFORM_OPERATOR_EMAILS` on the API service (several are separated by commas), and once that account exists, its Clerk user id in `PLATFORM_OPERATOR_USER_IDS` too, so the API recognizes it without asking Clerk. Any account whose verified address is listed is the operator, so deleting and recreating the account changes nothing. Gmail addresses match with or without dots; a `+tag` address is a different address, and a `+clerk_test` address is refused. The check needs the real `CLERK_SECRET_KEY` on the API, and production refuses to start without it. Production refuses to start with `ACCESS_APPROVAL=off`.

The operator has no company. Signing in as the operator opens one window, `/operator`, from every console address: the requests waiting for a decision, each with the company's name, its account email (marked when unverified or a test address), the recall, the company's plan and products, and a hint comparing the email domain with the company and websites the notice names. Approve when the domain matches the company, or after calling the contact number in the notice. Approving creates the company's product from the notice and opens it with its claim link, unless the company's plan has no room (it stays closed until they upgrade) or the notice gives no refund amount (it waits for the company to set one). Revoking pauses the product: its link and its claimants' links stop, the worker leaves its claims, and no new refunds start; approving again resumes the same product and link. One company at most runs each recall. To demo both sides, use two browsers (or a private window): Clerk keeps one session per browser.

New companies name themselves and pick Free or Growth at sign-up, then see only **Recall access**, Billing, and their name until you approve a request.

## 8. Demo data (local only)

`make seed` builds the laminator recall with 500 decided claims for local development. Since plan v3 it refuses `APP_ENV=prod`: it writes companies with an approval nobody gave, and a public claim link must never sit next to them. The live site starts empty and fills through the steps below. (Before plan v3 the seed ran once as a one-off job on the worker service; that took 14 minutes next to the database, against several Supabase rate-limit and timeout errors from a home connection.)

## 9. Check it end to end

1. In one browser, sign up as a company: name it and pick Free. Under **Recall access**, find a CPSC recall (for example "laminator") and ask for it.
2. In another browser, sign in as the operator and approve the request. The company's product opens with its claim link and QR code.
3. On a phone, scan the QR code, start a claim, and file it with its photos.
4. Within a minute the worker decides it. Open the claim in the console and read its trace. If it is approved, choose a bank transfer on the phone and finish Stripe's test onboarding; the payment job sends the transfer within 10 minutes (the platform's Stripe test balance must be funded).
5. Ask for a second recall on Free: the answer is the upgrade prompt. Upgrade under Billing with the test card 4242 4242 4242 4242 and ask again.
6. Generate the monthly report and download its PDF.

If anything fails, Render's logs are JSON lines with an `event` field: start with `http_request`, `worker_claim_done`, and any `level: error`.

## What the first deploy found

The first deploy (2026-10-21, observed timeline in `docs/traces/M6-deploy.md`) differed from the plan in these places. Each is fixed in the code or the steps above:

- **Both cron jobs would have crashed on every run.** They carried no graph settings, so they loaded the development Neo4j defaults, which production refuses. A local preflight of each service's real environment caught it before they ran; `render.yaml` now disables the graph for them.
- **The seed and a live agent worker raced.** The seed commits claims before deciding them, so the worker could have run the paid agent on demo data. The worker was paused by hand at the time; seeded claims now carry the seed's lease.
- **Storage rate limits.** Supabase answered 429 to a burst of storage calls. The adapter now resends a 429 up to three times; the seed also repeats its own delete-then-put after a timeout.
- **Supabase's direct host is IPv6-only.** Everything connects through the session pooler (step 1).
- **Render needs a payment card** before it creates paid services, and **auto-deploy needs Render's GitHub app**; until then, deploy by hand after a merge.
- **Vercel needs its GitHub app** to see the repository before it can link the project.
- **Clerk's development handshake redirected the homepage.** The homepage loads no Clerk script, so the proxy now skips `/`.
- **Clerk's widgets were unreadable in dark mode** (near-black text on the dark sidebar); they now take the dark palette.
- **The first live claim looped.** The agent never reads a claimant's text reply, so a reply re-ran the same facts and asked the same question again. Replies now correct the details asked about; a note alone goes to a person; a repeated question goes to a person.
- **Anyone could run anyone's recall.** An organization calling itself 3M opened the Primark recall. Companies now need the operator's approval (step 7).

## Costs

The free tiers cover the website, the API, the database, photos, the graph, sign-in, and Stripe test mode. The paid pieces are the Render worker (about $7 a month) and the two cron jobs (billed by the minute, typically cents a month), plus AI calls of about 0.2 cents per claim, all metered against the project's budget. The whole project has spent $1.63 on AI so far.
