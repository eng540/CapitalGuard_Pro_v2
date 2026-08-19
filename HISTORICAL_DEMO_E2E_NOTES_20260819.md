# Historical Signal Reconstruction — Demo E2E Notes

## Basis and scope

This fixture-driven pipeline uses synthetic, deterministic Telegram Export and 1-minute OHLCV data for software acceptance. It is not a claim about real Telegram history, real exchange prices, or live analyst performance. The demo channel is a shadow channel and the demo analyst/reviewer/trader are test identities.

The fixture covers two completed winners, one stop-loss outcome, one unfilled entry, an edited message, reply/update messages, a manual-close text, Arabic partial text, free-form commentary, an unparseable announcement, and a Telegram service message. The Export adapter ignores service messages, preserves reply references, and records `message_revision=1` plus `edited_date` for edited messages.

## Replay rules

The demo uses deterministic 1-minute OHLCV candles in UTC. `CandleCache` is keyed by asset, market, and open time. Observations after `replay_end` are rejected. A candle that crosses both a target and a stop is resolved with `PESSIMISTIC_SL_FIRST`, so the stop-loss is recorded and targets from the same candle are not credited.

A signal is eligible for historical ranking only when it has a verified trust tier, sufficient confidence, a verified activation, and a terminal verified outcome: SL or all declared targets. Unfilled signals remain visible in historical summaries but are excluded from terminal win-rate denominators. Manual or unverified evidence remains excluded from ranking.

## E2E acceptance

`tests/test_historical_reconstruction_e2e.py` proves the following path:

```text
demo Telegram Export
→ TelegramExportAdapter
→ manifest validation and VALIDATED import batch
→ immutable evidence
→ shared historical parser
→ FinancialConsistencyService
→ HistoricalSignal records
→ CHANNEL attribution and review
→ TRADER_FOLLOW historical wallet
→ 1m OHLCV CandleCache
→ Market Replay with pessimistic SL-first
→ HistoricalReputationSummary
→ isolation assertions
```

The expected demo summary is two winning signals, one losing signal, one unfilled signal, three rank-eligible terminal signals, a 66.6667% terminal win rate, and a demo PnL sum of 9.0000 percentage points under the declared weighted-target convention. These values are fixture-derived and must never be presented as live or real historical performance.

## Isolation gate

The E2E test asserts that the pipeline creates no live `Recommendation`, no live `UserTrade`, and no `PublicationDelivery` record. It does not start PriceStreamer, does not publish to Telegram, and does not write to live `AnalystStats`.

## Current gate

The software is **fixture-ready** after local gates: `142 passed, 1 skipped`, compileall passed, critical Flake8 passed, Bandit passed, and `git diff --check` passed. Real-data readiness remains a separate gate requiring an authorized source, real market-data coverage, ownership approval, and a successful dry-run/replay on a small sample.
