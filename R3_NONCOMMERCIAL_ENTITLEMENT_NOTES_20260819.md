# R3 Non-Commercial Entitlement Notes

## Delivered

This slice adds two append-only audit tables: `entitlement_grants` and `subscription_ledger_entries`. The service supports zero-cost Alpha grants, append-only revokes, effective feature checks, and idempotent retries. The service rejects its own commercial path when instantiated with `billing_enabled=True`; the production setting defaults to `BILLING_ENABLED=false`.

An admin-only `/grantalpha <telegram_user_id> <FEATURE1,FEATURE2> [key=...]` command records Alpha features without charging. The command is visible in `/admin` and uses an idempotency key. It does not contact a payment provider, create a checkout, process a webhook, or alter a commercial subscription.

## Safety boundary

The amount is always zero in this slice, provider is `INTERNAL`, and all reversal is represented by an appended `REVOKED` decision and `REVOKE` ledger entry. No row is deleted to reverse access. Future payment-provider events require a separate commercial gate and signed webhook/reconciliation design.

## Validation so far

Focused R3/R2 tests pass: entitlement grant/revoke/idempotency, analyst profile, discovery, and comparison tests. Compileall and critical Flake8 checks pass. Full-suite, Bandit, Alembic-head, PR/CI, and Railway checks remain the release gates.
