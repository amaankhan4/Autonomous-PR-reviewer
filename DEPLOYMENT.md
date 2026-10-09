# Deployment guide

This repository supports a portfolio/demo deployment without Docker:

- **One Vercel project:** frontend and API as Vercel Services, with the
  repository root selected.
- **Database:** hosted PostgreSQL.
- **Alternative API host:** Render Blueprint, using `render.yaml` at the
  repository root.

## Vercel Services deployment

The root `vercel.json` defines two services in one Vercel project:

- `frontend` handles all non-API paths, including React client-side routes.
- `backend` handles `/api/*`, preserving the FastAPI application's existing
  `/api/v1/*` route paths.

The browser calls `/api/v1/*` on the same domain, so leave
`VITE_API_BASE_URL` empty. No Vercel service binding is required: bindings are
for private, server-to-server service calls, and this application has none.

Configure the following Vercel environment variables for the demo profile:

```
DATABASE_URL=<hosted Postgres connection string>
SECRET_KEY=<long random value>
ENVIRONMENT=production
DEBUG=false
MOCK_GITHUB=true
LLM_PROVIDER=mock
VECTOR_STORE=memory
CELERY_TASK_ALWAYS_EAGER=true
SEED_DEMO_DATA=true
PUBLISH_REVIEWS=false
FRONTEND_URL=https://YOUR-PROJECT.vercel.app
CORS_ORIGINS=https://YOUR-PROJECT.vercel.app
```

Apply Alembic migrations to the hosted database before the first production
request (for example from a machine with `backend` dependencies installed):

```
cd backend
alembic upgrade head
```

The Vercel backend is appropriate for the inline/mock portfolio demo. A real
GitHub App deployment still requires an always-running worker, Redis, and
persistent vector storage.

## 1. Deploy the API

In Render, create a new Blueprint from this repository. The blueprint creates a
free FastAPI web service and Postgres database. It runs the demo using mock
GitHub and mock LLM providers. Reviews run inline (`CELERY_TASK_ALWAYS_EAGER`),
so a Redis instance and Celery worker are not needed for this public demo.

When prompted, leave `FRONTEND_URL` and `CORS_ORIGINS` unset until the Vercel
deployment exists. After it does, set both values to the exact Vercel URL, for
example `https://autonomous-pr-reviewer.vercel.app`, and redeploy the API.

`PUBLISH_REVIEWS=false` is intentional: this public demo never posts to a real
GitHub pull request. Do not change that setting until a GitHub App and webhook
secret have been configured.

## 2. Deploy the frontend

Create a Vercel project from the same repository and set **Root Directory** to
`frontend`. Add this production environment variable before deploying:

```
VITE_API_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com
```

Vite embeds this value at build time, so redeploy the frontend after changing
it. The `vercel.json` rewrite makes React Router routes load correctly on a
direct visit.

## 3. Verify

- Open `https://YOUR-RENDER-SERVICE.onrender.com/api/v1/health`.
- Open the Vercel URL and sign in with `demo@prreviewer.dev` / `demo12345`.
- Start a review and confirm that findings appear. The first API request after
  an idle period can be slow on a free web service.

For a real GitHub App, use a separate production environment with Redis,
persistent vector storage, a Celery worker, and non-mock provider credentials.
