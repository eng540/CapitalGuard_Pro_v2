# R2 Profile and Metrics Implementation Notes

## Implemented

The current R2 slice extends the existing analyst pipeline rather than creating a second performance source. `AnalystProfile` now supports public name, bio, specialty market, strategy style, and profile update time. `AnalystProfileService` creates or updates only the authenticated analyst's own profile and validates field lengths.

Telegram now exposes `/analyst_profile`. Without arguments it displays the current profile; with `name=... | bio=... | market=... | style=... | public=yes/no` it updates the authenticated analyst only. The command is protected by active-user and analyst-role checks and is listed in the role command directory.

`AnalystDiscoveryService` now accepts optional `window_days`, returns `latest_data_at`, `freshness_days`, `as_of`, and computes `risk_exposure_pct` as the sum of `abs(entry-stop_loss)/entry*100` for currently ACTIVE recommendations. The historical `exposure_proxy` remains in the contract for compatibility but is no longer the primary displayed metric.

`AnalystComparisonService` now exposes optional `window_days` in addition to the existing channel, market, asset, and explicit date filters. `/find_analysts days=30` and `/compare_analyst AN-000001 days=30 market=Futures asset=BTCUSDT` expose these constraints conservatively.

## Quality

The focused R2 suite currently passes: profile service/parser, discovery, comparison, and UX tests. The next gate is the full project suite, security scans, migration-head validation, then PR/CI and Railway verification.

## Boundaries

The risk metric is an approximate percentage-risk exposure, not capital allocation. Ranking remains sample-size gated. No payment, subscription, copy trading, or automatic execution behavior is included.
