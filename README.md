# TreasuryPilot AI

**Resolve the payment. Protect the cash.**

TreasuryPilot AI is an operator-first payment incident workflow for investigating supplier transfer uncertainty. It separates user claims from verified transfer evidence and blocks replacement payments while the original outcome is unresolved.

## Current implementation status

- **Implemented:** FastAPI application, bearer-token authentication with production-separated operator and policy-admin roles, SQLAlchemy entities and Alembic migrations, incident investigation, saved-beneficiary retrieval with PII-minimized evidence, operator-entered obligations, versioned per-currency reserve policies, persisted live-plan evaluation, audit events, a protected read-only approval-record endpoint and dashboard, Airwallex read adapter, an unexposed single-attempt transfer client, and a durable unexposed operation/reconciliation service; explicit status mapping, deterministic Decimal treasury evaluation, React/TypeScript dashboard, and a backend-only structured AI interpretation endpoint with a deterministic no-key fallback.
- **Tested locally:** Backend 64 passed, including persisted approval-record listing and secret-field omission; frontend 3 passed, including approval-center detail rendering, empty state, and no action controls; Playwright Chromium workflows 2 passed, including the captioned 3-minute-32-second submission recording; frontend production build succeeds; clean SQLite migrations succeeded; Docker Compose PostgreSQL stack starts and passes readiness checks. The backend rejects request bodies above 1 MiB; treasury planning applies fees per obligation and holds unknown-fee actions. The operation service enforces five-minute evidence freshness, a database-unique recovery reservation per original transfer, and a durable approval bound to the exact request, fresh original evidence, current policy version, and a separate operator/admin role. See [test record](docs/testing.md).
- **Tested against Airwallex sandbox:** Credentials are configured backend-only, but authentication returned HTTP 403 (`permission`); no successful read was verified. Playwright now explicitly blanks Airwallex credentials to prevent external calls. No transfer write was attempted.
- **Simulated:** Treasury Planning includes an explicit synthetic obligation plan and a full stateless recovery walkthrough: processing blocks replacement, a synthetic failure permits evaluation, reserve-aware planning defers a lower-priority obligation, an approval is bound to an exact action hash, and synthetic creation/verification are shown. It never calls Airwallex or mutates real incidents; its zero-fee assumption is not usable for live authorization.
- **Blocked:** Successful Airwallex sandbox reads, an account-verified pre-submit fee quote, approval-request/approval actions, recovery execution, and settlement verification. The running backend has credentials but Airwallex rejects authentication with HTTP 403; account permissions and beneficiary corridor/schema remain unverified. Official docs describe a pre-submit batch quote, but this app has not implemented or tested that draft-batch flow; see [API research](docs/official-api-research.md#batch-quote-alternative).
- **Not implemented:** Live AI provider verification, named-user identity/organization SSO, creating or approving live action requests, and transfer execution. The new approval view is read-only; the internal approval service has no request or approve API route. Production separates static operator and policy-admin bearer tokens; local development uses one operator token. Obligations are operator-entered and are not connected to a payable system.
- **Fail-closed live planning:** A persisted plan combines a fresh Airwallex balance, configured reserve policy, and entered obligations. It holds actions while fees are unknown, when a currency has multiple balance accounts, or when a policy/balance is missing. Its balance endpoint has not been tested against an actual Airwallex account. No transfer endpoint exists.

The HackerEarth listing currently gives the build phase as **October 27–November 13, 2026**, with submissions due November 13 at 18:29 UTC. Registration closes October 25 at 18:29 UTC (11:59 PM IST). The organizer landing page shows the broader event period through November 19. See [official event listing](https://www.hackerearth.com/community/challenges/hackathon/agentic-banking-hackathon/?register=true) and [research notes](docs/official-api-research.md).

## Architecture

- Frontend: React, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query.
- Backend: Python, FastAPI, Pydantic, SQLAlchemy, Alembic, HTTPX.
- Database: PostgreSQL for deployment; SQLite is the local default for the first milestone.
- Airwallex: backend-only sandbox credentials; sandbox base URL by default.
- Money: Python `Decimal`; currencies are evaluated independently. Unknown fees, stale balance data, and missing transfer evidence block eligibility.

See [architecture](docs/architecture.md), [official API research](docs/official-api-research.md), and [financial safety](docs/financial-safety.md).

The current status of each requested acceptance gate is recorded in [acceptance status](docs/acceptance-status.md).

## Prerequisites

- Python 3.11+
- Node.js 22.12+
- PostgreSQL 15+ (optional for local SQLite development)

## Local setup

From the repository root:

```sh
cp -n .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-test.lock
.venv/bin/pip install --no-deps --no-build-isolation -e backend
cd frontend && npm install && cd ..
```

Set a long random `APP_API_TOKEN` in `.env`. Do not use the example placeholder. The token is required by the frontend for protected routes and is held only in browser session storage. Local development permits this operator token to change policies; production requires a separate `APP_ADMIN_TOKEN` for those changes. Static bearer tokens are not named-user identity or organization SSO.

When `APP_ENV=production`, the backend refuses to start unless `APP_API_TOKEN` and the distinct `APP_ADMIN_TOKEN` are each at least 32 characters and not placeholders and `DATABASE_URL` uses PostgreSQL. Generic `postgresql://` connection strings are normalized to the installed psycopg v3 driver. Every non-empty `FRONTEND_ORIGINS` entry must be a bare HTTPS origin. The admin token is required for policy changes; local development retains a single-operator shortcut.

Set `DATABASE_URL` to a PostgreSQL connection string for persistent development/deployment. If omitted, local development uses `backend/treasurypilot.db` when started from `backend`.

AI-assisted incident interpretation is optional. To enable it locally, set `AI_PROVIDER=openai`, `AI_API_KEY`, and optionally `AI_MODEL` in the backend environment (Docker Compose reads them from `.env`). The API key is never exposed to the browser. Without these settings, the UI labels the provider as unavailable and shows a deterministic evidence-gathering next step.

### Airwallex sandbox

Create sandbox API credentials in the Airwallex sandbox dashboard and set `AIRWALLEX_CLIENT_ID` and `AIRWALLEX_API_KEY` in the backend environment. Never use frontend environment variables for these credentials. API keys must be restricted to required scopes. Set optional `AIRWALLEX_LOGIN_AS` when a scoped key linked to multiple accounts needs an account selected during authentication. The adapter supports authentication and read-only balance/transfer/beneficiary retrieval. A single-attempt transfer-create method exists at the client layer for future gated use, but no application route can call it yet. Current sandbox authentication returns HTTP 403 (`permission`), so reads have not been verified against the account.

## Run

Terminal 1:

```sh
cd backend
DATABASE_URL=sqlite:///./treasurypilot.db ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

Terminal 2:

```sh
cd frontend
npm run dev
```

For a containerized local stack, configure `APP_API_TOKEN` in `.env` and run `docker compose up --build`. Docker Compose starts PostgreSQL, the backend, and the frontend reverse proxy; the browser uses the same-origin `/api` path. Stop with `docker compose down`. The database volume is retained unless explicitly removed.

Open <http://localhost:5173>, enter `APP_API_TOKEN`, then create an incident. With Airwallex credentials absent, investigation records unavailable external evidence and classifies the transfer outcome as unknown; recovery stays blocked. With credentials configured, the backend can attempt the documented read-only investigation calls.

## Database

Alembic configuration is in `backend/alembic.ini`. Set `DATABASE_URL`, then run:

```sh
cd backend
DATABASE_URL=sqlite:///./treasurypilot.db ../.venv/bin/alembic upgrade head
```

## Tests and build

```sh
cd backend && ../.venv/bin/pytest -q
cd frontend && npm test -- --run
cd frontend && npm run test:e2e
cd frontend && npm run build
```

Local unit/API tests use deterministic logic and local database fixtures. They do not prove Airwallex sandbox behavior. Do not call an integration sandbox-tested until an authenticated read/write has been verified on the actual sandbox account.

## Demo

See [demo script](docs/demo-script.md). The local walkthrough demonstrates evidence separation and a separate, fully labeled synthetic recovery scenario with reserve checks, exact-action approval binding, and simulated verification. It is a backup demo and does not create or transition a transfer in Airwallex.

The project description and final delivery checklist are in the [submission package](docs/submission-package.md). A captioned 3-minute-32-second local video and source archive are prepared under `submission/`. The entrant reports solo registration and organizer eligibility confirmation. Publishing a repository and video, checking the exact HackerEarth profile details, and provisioning a hosted demo remain outstanding.

## Deployment

See [deployment notes](docs/deployment.md) and the [Render Blueprint](render.yaml). No live deployment has occurred. The frontend image serves the app and proxies same-origin `/api` requests to the private backend service; production does not embed a backend URL in its JavaScript bundle. Before production use, replace static role tokens with named-user organizational identity and complete a security review. No transfer execution endpoint exists.

## Known limitations

- Airwallex reads and the low-level single-attempt create adapter have no actual sandbox verification. Transfer creation is not exposed through an application workflow.
- Status mapping preserves unknowns; `CANCELLED` does not prove failure.
- Operator-entered obligations are persisted, listed, and audited, and the live treasury-plan endpoint evaluates them against fresh balances and saved per-currency policies when Airwallex reads succeed. These records are not verified against an invoice/payables source; in this workspace the plan fails closed because Airwallex authentication returns HTTP 403.
- Exact-action approval binding and single-use consumption are implemented and tested in an internal service. The protected `GET /approvals` route and UI review persisted approval records read-only; approval-request, approve, and transfer-execution routes are not exposed.
- Alembic migrations have been applied successfully to clean temporary SQLite and PostgreSQL databases and the running Compose PostgreSQL database.
- Single-token authentication is appropriate only for local evaluation.
- AI interpretation uses OpenAI Responses Structured Outputs only when `AI_PROVIDER=openai` and a backend-only `AI_API_KEY` are configured. The model receives no tools, is asked not to calculate or authorize, and cannot alter backend evidence or deterministic policy results. With no provider configuration, the endpoint returns a labeled deterministic next step. No live AI provider call has been verified.
- No production deployment or end-to-end sandbox run has been verified. The current browser workflow is a local blocked-recovery walkthrough, not full resolution.
