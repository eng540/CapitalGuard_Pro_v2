# CapitalGuard Web SaaS

`frontend/` is a standalone Node 22 service providing the CapitalGuard Web SaaS. It is deliberately separated from the Python Core and Telegram Bot: the web layer owns presentation, role-aware tRPC APIs, and assistant features, while CapitalGuard Core remains the source of truth for live recommendations, trades, Temporal Decisioning, historical evidence, and Market Replay.

## Railway deployment

Create a **second Railway service** from this repository and set its **Root Directory** to `frontend`. Railway then reads `frontend/railway.toml` and runs:

```text
npx --yes pnpm@10.4.1 install --frozen-lockfile && npx --yes pnpm@10.4.1 build
node dist/index.js
```

The pinned `npx` invocation intentionally avoids a Corepack signature issue observed during the verified export build. The runtime starts the already-built Node entrypoint directly and does not require package installation on every request.

The application already binds to Railway's assigned `PORT`; no custom Dockerfile is needed for this ordinary Node service. Do **not** replace the existing Core service or deploy this directory over the Telegram Bot service.

## Required environment variables

| Variable | Scope | Purpose |
|---|---|---|
| `DATABASE_URL` | Server only | Dedicated MySQL/TiDB database for Web SaaS users, roles, UI projections, and audit views. It must not be the Core PostgreSQL connection string. |
| `JWT_SECRET` | Server only | Signs Web SaaS sessions. Use a unique high-entropy secret. |
| `CAPITALGUARD_CORE_BASE_URL` | Server only | Base URL of the existing Core API, for example `https://capitalguardprov2-production-b4ea.up.railway.app`. |
| `CAPITALGUARD_CORE_API_KEY` | Server only | A narrow service key used only by the server-side read adapter. Never use a `VITE_` prefix. |
| `VITE_APP_TITLE` | Public build value | Browser title and UI identity. |

The initial template still includes Manus OAuth variables: `VITE_APP_ID`, `OAUTH_SERVER_URL`, and `VITE_OAUTH_PORTAL_URL`. They are valid only when deploying through the Manus environment. For an independent Railway/Vercel release, replace the template OAuth adapter with a CapitalGuard-owned login provider or Telegram Mini App `initData` verification before going live.

## Core integration safety

The Web service calls Core from the server only. The browser never receives `CAPITALGUARD_CORE_API_KEY`, Telegram tokens, exchange secrets, or direct PostgreSQL access. Current Web routes are read-only (`health`, price, signal details, and TMA portfolio); they do not create a recommendation, user trade, publication outbox message, or copy-trading order.

## Vercel note

This directory is currently packaged as an Express/tRPC Node service. The included Railway configuration is the supported deployment path. A Vercel deployment requires a separate conversion of the Express entrypoint to Vercel Functions before it can safely serve authenticated tRPC operations; deploying it as a static Vite site would disable the protected API layer.
