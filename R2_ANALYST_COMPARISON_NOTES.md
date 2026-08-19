# R2 Analyst Comparison Notes

**Branch:** `feature/r2-performance-comparison-20260819`

## Scope

This slice adds read-only channel comparison for an analyst. It uses the canonical `ChannelCatalog` and `RecommendationChannelRef` identities, so one recommendation published to multiple channels is represented in each channel's local sample without creating duplicate recommendation entities.

The comparison service supports channel-code filtering, asset filtering, market filtering, creation time bounds, bounded result size, and per-channel metrics: sample size, Win Rate, cumulative PnL percentage, maximum drawdown proxy, and sample eligibility.

The new `/compare_analyst CODE` command exposes the comparison with an explicit descriptive disclaimer. It accepts the analyst code or opaque public reference and never exposes internal database identifiers as the lookup contract.

## Guardrails

A channel is not ranked from a small sample. The default minimum comparison sample is five valid closed recommendations. The result preserves sample size and drawdown next to Win Rate and PnL. This is descriptive analytics, not a recommendation, subscription, payment, Copy Trading, or automatic execution feature.

## Quality evidence

After this slice: **98 tests passed, 1 skipped**, compileall passed, touched-module critical Flake8 passed, Bandit high-severity scan passed, and git diff check passed. Existing parser warnings and the pre-existing skipped parser test remain unchanged.

## Next measurement work

The next performance slice should replace the active-count exposure proxy with a risk/notional-aware measure, add explicit measurement windows and data freshness, and add channel/market comparison tests with incomplete and mixed lifecycle records.
