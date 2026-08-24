# G6 Extraction & Semantic Materialization — Implementation Report

## Status

`IMPLEMENTED ON BRANCH — READY FOR REVIEW`

The implementation is based on the merged G6 Replay in `main` at `83460dae102ac1126d1cd463ff344391919f7b0d`. G5 materialization and G6 Replay were reused; they were not rebuilt.

## Implemented scope

The shared historical parser now preserves target suffixes such as `78K` as `78000` and supports indexed forms such as `TP1:78K` without consuming the first digit as the target index.

A new application service, `HistoricalSemanticMaterializationService`, reuses the existing G2 interpretation, G3 financial-candidate extraction, G4 draft, and G1 revision provenance contracts. It produces a canonical semantic projection inside the existing `HistoricalRecommendationDraft.evidence_chain_json`. It does not create a `HistoricalSignal`, invoke Replay, or create live trading objects.

The service supports text-only materialization, image candidate materialization, text-plus-image comparison, conflict preservation, missing-field classification, field-level evidence, modality attribution, extractor version, media identity, and idempotent reprocessing.

Historical Telegram forwarding now preserves photo identity in the existing receipt metadata and source revision provenance. Both direct automatic historical intake and manual historical forwarding call the same semantic materialization helper. Vision remains an external parser; image results remain candidates until existing review/adjudication controls accept them.

The Vision response contract now carries optional leverage and explicitly instructs the model not to infer leverage when it is absent or unclear.

Related-message materialization requires an accepted existing message relationship and preserves the contributing revision IDs rather than flattening source history without attribution.

## Transaction and boundary behavior

No new transaction manager was introduced. The implementation uses the caller-provided SQLAlchemy session and performs flushes only. It does not commit or rollback a caller-owned transaction. The semantic service remains upstream of G5 signal materialization and G6 Replay.

## Tests

The branch adds contract coverage for:

- `BTCUSDT Futures LONG Entry 77K SL 76K TP 78K Leverage 5X`.
- Missing financial values and fail-closed incomplete status.
- Image-only candidates with media provenance.
- Text-plus-image conflicts without silently choosing a winner.
- Idempotent reprocessing of the same revision and image.
- Accepted related-message context and field-level revision attribution.
- Telegram photo identity preservation.
- Optional Vision leverage and non-inference when missing.
- No `HistoricalSignal` creation before G5 review/materialization.

Final local validation:

```text
299 passed, 1 skipped
compileall: PASS
git diff --check: PASS
pip-audit: PASS — No known vulnerabilities found
bandit on changed application paths: PASS
```

The repository-wide `flake8` baseline remains non-zero because of pre-existing style violations in unrelated files. No unrelated style cleanup was included in this branch.

## Explicit non-scope

This change does not rebuild or alter `HistoricalSignal`, `HistoricalSignalMaterialization`, `HistoricalMarketReplayService`, `HistoricalReplayRun`, `HistoricalMarketEvidence`, Binance historical provider behavior, Ranking, Trust, Reputation, Risk, Trading, live execution, or G7.

External live Telegram/Vision credentials were not used in local tests. The code path is wired through the existing Telegram and Vision adapters, while deterministic tests use contract payloads and existing database fixtures.
