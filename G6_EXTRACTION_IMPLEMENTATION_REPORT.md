# G6 Extraction & Semantic Materialization — Implementation Report

## Status

`IMPLEMENTED ON BRANCH — READY FOR REVIEW`

The implementation is based on the merged G6 Replay in `main` at `83460dae102ac1126d1cd463ff344391919f7b0d`. G5 materialization and G6 Replay were reused; they were not rebuilt.

## Implemented scope

The shared historical parser now preserves target suffixes such as `78K` as `78000` and supports indexed forms such as `TP1:78K` without consuming the first digit as the target index.

A new application service, `HistoricalSemanticMaterializationService`, reuses the existing G2 interpretation, G3 financial-candidate extraction, G4 draft, and G1 revision provenance contracts. It produces a canonical semantic projection inside the existing `HistoricalRecommendationDraft.evidence_chain_json`. It does not create a `HistoricalSignal`, invoke Replay, or create live trading objects.

The service supports text-only materialization, image candidate materialization, text-plus-image comparison through the historical handler helper, conflict preservation, missing-field classification, field-level evidence, modality attribution, extractor version, media identity, normalized values, validation status, final semantic status, and idempotent reprocessing.

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
303 passed, 1 skipped
compileall: PASS
git diff --check: PASS
pip-audit: PASS — No known vulnerabilities found
bandit on changed application paths: PASS
```

Proof Closure coverage includes a committed visible PNG fixture containing the BTC/Futures/LONG/Entry 77K/SL 76K/TP 78K/Leverage 5X signal, passed through the existing Vision adapter with deterministic mocked provider boundaries, the existing `ImageParsingService` file-id proxy path, and the historical handler materialization helper. It also proves a handler-level Text + Image conflict with separate modality evidence. The proof verifies BTC, Futures, LONG, 77000, 76000, 78000, and leverage 5, then persists IMAGE candidates and provenance in the existing G4 Draft boundary. No external Telegram or LLM credentials were used.

The repository-wide `flake8` baseline remains non-zero because of pre-existing style violations in unrelated files. No unrelated style cleanup was included in this branch. The PR workflow checks remain green; `flake8` is not a required workflow check for this repository.

## Explicit non-scope

This change does not rebuild or alter `HistoricalSignal`, `HistoricalSignalMaterialization`, `HistoricalMarketReplayService`, `HistoricalReplayRun`, `HistoricalMarketEvidence`, Binance historical provider behavior, Ranking, Trust, Reputation, Risk, Trading, live execution, or G7.

External live Telegram/Vision credentials were not used in local tests. The code path is wired through the existing Telegram and Vision adapters, while deterministic tests use contract payloads and existing database fixtures.

## Final Proof Closure Report

### 1. Existing capabilities reused

The implementation reuses the existing Telegram historical forwarding handler, receipt and canonical message foundation, revision provenance, content interpretation, financial candidate extraction, G4 adjudication draft, Vision adapter, G5 boundary, and merged G6 Replay downstream.

### 2. Files changed

The changed files are the parser and candidate normalization services, historical forwarding/foundation services, the historical Telegram handler, the new semantic materialization service, the Vision response schema/serializer/prompt, contract tests, real-image path tests, and the visible PNG fixture committed under `tests/fixtures/g6_btc_signal.png`.

### 3. Exact gaps fixed

The fixed gaps are target suffix normalization, image media identity preservation, optional Vision leverage, text/image candidate materialization, conflict preservation, missing-field handling, related-message attribution, field-level normalization and validation evidence, and handler-level image/multimodal proof.

### 4. Architecture path

```text
Telegram source
→ raw receipt and revision
→ text and/or existing Vision adapter
→ candidates
→ validation and normalization
→ field evidence/provenance
→ existing G4 Draft evidence chain
→ G5 where applicable
→ existing G6 Replay downstream
```

### 5. Text proof

The contract input `BTC Futures LONG Entry 77K SL 76K TP 78K Leverage 5X` produces `BTC`, `FUTURES`, `LONG`, `77000`, `76000`, `78000`, and `5`. The parser regression and semantic materialization tests assert that `78K` is never reduced to `8000`.

### 6. Real image proof

A committed visible PNG fixture containing the signal labels is passed through the existing Vision adapter with deterministic provider boundaries, then through the existing `ImageParsingService` file-id proxy and the historical handler materialization helper. The resulting image candidates contain normalized BTC/Futures/LONG/77000/76000/78000/5 values and media/provider/model provenance. No external credentials are used in CI.

### 7. Text+Image proof

The handler-level multimodal test supplies text entry `77000` and image entry `78000`; the result is `CONFLICT`, canonical entry is null, and both TEXT and IMAGE evidence remain attached.

### 8. Missing/ambiguous proof

The incomplete input `BTC LONG TP 78K` produces `INCOMPLETE` with missing entry and stop loss and does not invent either value. Conflicting values do not silently select a winner.

### 9. Related-message proof

An accepted existing message relationship is required before combining revisions. The combined result preserves anchor and related revision IDs and field evidence points to the revision that supplied the field. The test is deterministic database integration rather than a live Telegram session.

### 10. Field-level provenance proof

Each candidate evidence entry contains raw value JSON, normalized value, source span or image field location, modality, extractor version, provenance metadata, candidate validation status, review status, and final semantic status. Text includes content hash and revision identity; image includes media identity when supplied.

### 11. Reprocessing/idempotency proof

Repeated materialization for the same revision and image payload returns the same projection and does not add duplicate candidates. This is local transactional proof; distributed process-restart and concurrent worker semantics remain non-blocking operational follow-ups.

### 12. G5 boundary proof

The semantic service creates or reuses a G4 Draft and stores the projection in its evidence chain. It does not create a HistoricalSignal and does not invoke G5 materialization automatically.

### 13. G6 Replay boundary proof

The extraction implementation does not alter or invoke Replay core, ReplayRun, Market Evidence, Binance provider, Ranking, Trust, Risk, Trading, or G7. It produces upstream semantic input only.

### 14. Tests executed

```text
303 passed, 1 skipped
```

The suite includes text, image, file-id proxy, handler helper, Text+Image conflict, missing fields, related messages, provenance, idempotency, AI response contract, and G5 boundary tests.

### 15. CI result

After the final proof commits, PR #338 reports six successful checks: `test` push/pull request, `fresh-postgres-migration` push/pull request, and `web-contract-coverage` push/pull request.

### 16. Remaining findings

The repository-wide flake8 command remains non-zero because of pre-existing style violations in unrelated files; it is not a required PR workflow check. Live external Telegram/Vision credentials were not used, so provider availability, rate limits, and real account permissions remain deployment-level verification items. Related-message proof remains deterministic integration rather than a live Telegram session, and distributed concurrency/restart idempotency remains an operational follow-up.

### 17. Final classification

**G6 EXTRACTION ACCEPTABLE WITH NON-BLOCKING FINDINGS**
