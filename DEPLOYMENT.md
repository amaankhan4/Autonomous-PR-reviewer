# Deployment guide

This repository supports a portfolio/demo deployment without Docker:

- **Frontend:** Vercel, with `frontend` selected as the Root Directory.
- **API and database:** Render Blueprint, using `render.yaml` at the repository root.

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
