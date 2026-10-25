# Deployment

Deployment is **not performed**. There are no deployment credentials or database service configured.

## Platform choice

Render is the prepared option for this repository: it supports Docker services, managed PostgreSQL, private services, runtime environment secrets, and configurable HTTP health checks. The current [Render Blueprint](../render.yaml) defines a private backend, PostgreSQL database, and public frontend service. Automatic deployments are turned off in the blueprint. Review current service and database pricing before creating resources. See [Render Blueprint reference](https://render.com/docs/blueprint-spec), [Docker services](https://render.com/docs/docker), [environment variables](https://render.com/docs/configure-environment-variables), and [health checks](https://render.com/docs/health-checks).

The Blueprint passes the official Render JSON Schema structural check. The Render CLI was also tried, but its `blueprints validate` command requires a Render workspace ID, which is not configured here. Workspace conflict checks and resource-plan validation therefore remain unverified. See Render's [Blueprint validation docs](https://render.com/docs/blueprint-spec#validating-blueprints).

This design is not a zero-cost persistent deployment: Render does not offer a free private-service plan, and its free Postgres instances expire 30 days after creation. Free web services can sleep after 15 minutes idle. Confirm the selected plans and current cost in the Render dashboard before provisioning; this workspace has not created any Render resource. See [Render free-instance limits](https://render.com/docs/free) and [current compute pricing](https://render.com/pricing).

Railway and Fly.io remain alternatives but are not evaluated in this milestone. Cost and plan availability are time-sensitive and must be rechecked before choosing a paid service.

The application includes Dockerfiles, Docker Compose, and a Render Blueprint for deployment preparation. Backend runtime versions are pinned in `backend/requirements.lock`; `backend/requirements-test.lock` adds pinned test tools. The Docker build installs the runtime lock, and local setup uses the test lock. Update these files deliberately with `pyproject.toml` when changing backend dependencies. Docker ignore files exclude local environment files, databases, dependencies, and generated output; the backend container runs as a non-root user. The frontend image proxies same-origin `/api` requests to the backend over the service network, so browser JavaScript has no baked-in backend hostname. The proxy removes the browser `Origin` header; the production backend therefore has an empty CORS allowlist and rejects cross-origin browser calls. Production setup requires:

1. A managed PostgreSQL database and its private connection string.
2. Backend-only `DATABASE_URL`, `APP_API_TOKEN`, `APP_ADMIN_TOKEN`, `APP_ENV=production`, `FRONTEND_ORIGINS`, and optionally sandbox Airwallex and AI secrets. The Render Blueprint prompts for two distinct long random tokens. Enter the operator token in the app for normal operations and the admin token only when changing treasury policies. For a multi-account scoped Airwallex key, set `AIRWALLEX_LOGIN_AS` to the target account ID. Set `AI_PROVIDER=openai`, `AI_API_KEY`, and optionally `AI_MODEL` only in the backend environment; never set the provider key in frontend build variables.
3. Apply Alembic migrations before starting the backend.
4. The frontend web service checks `/api/health/ready` through its same-origin proxy, verifying the UI proxy, backend, and database together. The private backend supports Render's default TCP check; Render does not offer HTTP health checks for private services. `/api/health` is liveness only.
5. Configure the frontend service's `BACKEND_HOSTPORT` reference to the backend's private host and port.
6. Configure HTTPS. Do not expose financial operations without authenticated, server-side authorization.

Production configuration is validated at backend startup. Startup fails unless `APP_API_TOKEN` is a non-placeholder value at least 32 characters long, `DATABASE_URL` uses PostgreSQL and is normalized to the installed `psycopg` v3 driver, and every non-empty `FRONTEND_ORIGINS` entry is a bare HTTPS origin. The Render Blueprint leaves the CORS allowlist empty because the UI proxies API calls same-origin and strips `Origin`. Airwallex credentials may remain unset for a disconnected, fail-closed deployment; live integration routes stay unavailable until both backend-only credentials are configured.

No hosting account, database instance, or live URL has been created or verified.

## Current workspace runtime

The current-source Compose stack was rebuilt on October 25 and is running locally at `http://127.0.0.1:8000` (backend) and `http://127.0.0.1:5173` (frontend). PostgreSQL reports healthy, readiness returns HTTP 200 directly and through the frontend proxy, and a bearer-authenticated incident-list request returned HTTP 200. The ignored root `.env` file supplies local backend-only credentials.

The current Compose backend receives both Airwallex variables from the ignored root `.env`, whose file permissions are `0600`. A fresh backend-only login check on October 25 returned HTTP 403 with provider code `unauthorized`; the balance request was not attempted. No successful balance, transfer, or beneficiary read has been verified. Do not move the keys to frontend build variables. The integration status remains configured but unverified, and balance reads fail closed.

Docker Compose supplies the frontend container with the internal backend host and port. The Render Blueprint supplies the private backend host/port through a service reference. The Nginx configuration adds frame/content-type/referrer protections and a same-origin content security policy.
