# CapitalGuard Web SaaS — Environment Variables

This file is intentionally visible in GitHub. It explains every variable needed by the standalone `frontend` service and never contains a real credential.

| Variable | Required | Where to set it | Purpose |
|---|---:|---|---|
| `DATABASE_URL` | Yes | Railway/Vercel server environment | Dedicated PostgreSQL database for the Web service. Do not point it at Core PostgreSQL. |
| `JWT_SECRET` | Yes | Server environment only | Signs web-session cookies. Generate a unique random value. |
| `CAPITALGUARD_CORE_BASE_URL` | Yes | Server environment only | The Core URL: `https://capitalguardprov2-production-b4ea.up.railway.app`. |
| `CAPITALGUARD_CORE_API_KEY` | Yes | Server environment only | Read-only Core service key. Never use a `VITE_` prefix. |
| `CAPITALGUARD_WEB_APP_ID` | No | Server environment | Stable, non-secret issuer label for Web session JWTs. Defaults to `capitalguard-web`. |
| `CAPITALGUARD_OWNER_TELEGRAM_ID` | Required for Web owner actions | Server environment only | Numeric Telegram ID of the platform owner. It promotes only the matching `telegram:<id>` Web session to `admin`; it is not a bot token. |
| `VITE_APP_TITLE` | Yes | Browser build environment | Browser-visible application title. |
| `VITE_APP_LOGO` | Optional | Browser build environment | Public logo URL only. |

## Railway

Create a **separate Web PostgreSQL service** and a separate Web application service with root directory `frontend`. Railway sets `PORT` automatically; do not create it manually. Reference the Web PostgreSQL URL as `DATABASE_URL`, then deploy. Keep the Core/Bot service and Core PostgreSQL unchanged.

The committed `frontend/drizzle/0000_*.sql` migration creates only `web_*` tables and web enums. The service runs `db:migrate` before startup; it does not generate or alter any Core migration.

## Telegram-first login

Web authenticates users from Telegram Mini App `initData`. The browser sends this data to the Web server; Web forwards it to Core with the server-only Core service key. Core verifies Telegram's HMAC using the bot token already owned by Core, and Web creates its own signed, HttpOnly session only if Core confirms the payload and the `auth_date` is fresh. Do **not** add `OAUTH_SERVER_URL`, `VITE_OAUTH_PORTAL_URL`, `VITE_APP_ID`, or a Telegram Bot Token to Railway for this path.

For the Core-owned live read models and audited Web commands, set the same rotated service-secret value as `API_KEY` in the **Core** Railway service and as `CAPITALGUARD_CORE_API_KEY` in the **Web** Railway service. The Core variable is never browser-visible; the Web service forwards it only in server-to-server `Authorization: Bearer` requests.

`CAPITALGUARD_ENABLE_LEGACY_OAUTH` remains disabled by default. Do not enable it on Railway unless a separately configured, real OAuth provider is introduced and reviewed.

The Mini App must be configured in BotFather with the final HTTPS Railway domain or custom domain. A normal browser intentionally receives a prompt to open the application through the CapitalGuard bot.

## Vercel

Import the repository with root directory `frontend`. Configure the same variables in Project Settings → Environment Variables. `CAPITALGUARD_CORE_API_KEY`, `JWT_SECRET`, and `DATABASE_URL` must remain server-only.

## Smart Dropzone

The current implementation uses Manus built-in AI integration when it runs in Manus. Before enabling the Smart Dropzone on standalone Railway or Vercel, either connect a server-side AI provider through a dedicated adapter or hide the feature. Do not expose any AI provider key to the browser.
