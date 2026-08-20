# CapitalGuard Web SaaS — Environment Variables

This file is intentionally visible in GitHub. It explains every variable needed by the standalone `frontend` service and never contains a real credential.

| Variable | Required | Where to set it | Purpose |
|---|---:|---|---|
| `DATABASE_URL` | Yes | Railway/Vercel server environment | Dedicated MySQL/TiDB database for the web service. Do not point it at Core PostgreSQL. |
| `JWT_SECRET` | Yes | Server environment only | Signs web-session cookies. Generate a unique random value. |
| `CAPITALGUARD_CORE_BASE_URL` | Yes | Server environment only | The Core URL: `https://capitalguardprov2-production-b4ea.up.railway.app`. |
| `CAPITALGUARD_CORE_API_KEY` | Yes | Server environment only | Read-only Core service key. Never use a `VITE_` prefix. |
| `VITE_APP_ID` | Required for current OAuth | Browser build environment | OAuth application identifier. |
| `OAUTH_SERVER_URL` | Required for current OAuth | Server environment | OAuth server base URL. |
| `VITE_OAUTH_PORTAL_URL` | Required for current OAuth | Browser build environment | OAuth login portal URL. |
| `VITE_APP_TITLE` | Yes | Browser build environment | Browser-visible application title. |
| `VITE_APP_LOGO` | Optional | Browser build environment | Public logo URL only. |

## Railway

Create a **separate** Railway service with root directory `frontend`. Railway sets `PORT` automatically; do not create it manually. Add the variables above in the service settings, then deploy. Keep the Core/Bot service unchanged.

## Vercel

Import the repository with root directory `frontend`. Configure the same variables in Project Settings → Environment Variables. `CAPITALGUARD_CORE_API_KEY`, `JWT_SECRET`, and `DATABASE_URL` must remain server-only.

## Smart Dropzone

The current implementation uses Manus built-in AI integration when it runs in Manus. Before enabling the Smart Dropzone on standalone Railway or Vercel, either connect a server-side AI provider through a dedicated adapter or hide the feature. Do not expose any AI provider key to the browser.
