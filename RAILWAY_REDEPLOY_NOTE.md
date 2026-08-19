# Railway Redeploy Marker

This file intentionally contains deployment metadata only. It does not alter application behavior, dependencies, migrations, routes, secrets, or runtime configuration.

The commit containing this marker is used to re-trigger Railway GitHub auto-deploy after the Railway deployment queue incident. After the resulting deployment becomes Active, run `bash scripts/railway_smoke.sh https://capitalguardprov2-production-b4ea.up.railway.app` and record the deployment logs and health result.
