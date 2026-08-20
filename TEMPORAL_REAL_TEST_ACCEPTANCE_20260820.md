# Temporal Real Test Acceptance — 2026-08-20

## Executive result

نتائج الاختبار تؤكد أن طبقة Temporal Decisioning تعمل في الإنتاج التجريبي بصورة صحيحة في أهم نقطة: **وقت إعادة التوجيه لا يطغى على وقت الرسالة الأصلية**. الرسائل الثلاث وصلت إلى البوت الآن، لكنها لم تتحول إلى توصيات حية لأن أعمارها الفعلية كانت أكبر من نافذة live البالغة 180 ثانية.

## Case assessment

| Case | Source age | Observed mode | Observed route | Assessment |
|---|---:|---|---|---|
| LSKUSDT closed | 22,168,339 sec ≈ 256.6 days | `CLOSED_EVENT` | `CLOSED_EVENT` | Correct temporal classification. It is a terminal historical event, not a new live recommendation. |
| SOLUSDT labelled LIVE | 13,332,552 sec ≈ 154.3 days | `HISTORICAL_RECONSTRUCTION` | `HISTORICAL_CANDIDATE` | Correct. The word `LIVE` belongs to the original channel card; it does not describe the current ingestion context. |
| BTCUSDT pending recommendation | 54,459 sec ≈ 15.1 hours | `HISTORICAL_RECONSTRUCTION` | `HISTORICAL_CANDIDATE` | Correct under a 180-second live window. The existing Recommendation ID is source metadata, not proof that a new live entity was created. |

All three records show:

```text
accepted=1
rejected=0
parsed=1
partial_or_unparsed=0
replay_status=REPLAY_PENDING
```

The system also correctly preserved source trust: the ADNAN source remained `UNCLAIMED`, while Crypto Radar remained `CANONICAL`. No live Recommendation, UserTrade, or Publication Outbox record was created by the historical layer.

## What the result proves

The test proves that the architecture now distinguishes these concepts:

```text
message text says LIVE
≠
message is live now
```

The decision is being made from source age and event semantics. The LSK terminal message is classified as `CLOSED_EVENT`; old source messages are classified as `HISTORICAL_RECONSTRUCTION`; and the replay remains pending because no historical OHLCV coverage has yet been attached.

## Important financial consistency finding

The LSK message contains a potential financial inconsistency that should be preserved as a review flag before replay acceptance:

```text
LONG
Entry: 0.21000
Exit: 0.19500
Stop: 0.19500
Displayed result: +0.95%
```

For a simple full LONG close at 0.19500 after entry at 0.21000, the price move is negative, approximately -7.14%, not +0.95%. This does not prove that the source is fraudulent: it may represent partial exits, a different weighted average, leverage/fee treatment, or a source formatting error. However, the system must not silently accept the displayed result as the reconstructed financial result.

The correct future status is:

```text
SOURCE_ASSERTED_RESULT
MARKET_REPLAY_PENDING
FINANCIAL_CONSISTENCY_REVIEW
```

The LSK text also reports updates using time-of-day only, while the trade spans multiple days. Event ordering therefore requires a date-aware reconciliation step; `02:30` and `07:54` cannot be safely ordered across a multi-day trade without the source date or reply chain.

## Gap identified in the current preview

The current preview is correct but still too terse for operational trust. It should add:

```text
source_time
received_time
market_as_of
source_age_human
current_price_vs_source_price
financial_consistency
source_assertion_status
parent_timeline_status
```

For a card containing the original word `LIVE`, the UI should explicitly show:

```text
Original label: LIVE
Temporal decision: HISTORICAL_RECONSTRUCTION
Reason: SOURCE_AGE_EXCEEDS_LIVE_WINDOW
```

This prevents the user from interpreting the source's old status label as the platform's current decision.

## Acceptance decision

The Temporal routing gate passes for source-time classification, stale-message isolation, terminal-event classification, source trust preservation, and live-entity isolation.

The final Historical Reconstruction gate does not pass yet because `REPLAY_PENDING` is expected until OHLCV data is supplied, and the LSK example requires Financial Consistency Review and date-aware Timeline Reconciliation before any reputation or performance metric is accepted.

## Next implementation priority

The next small, high-value slice is not another router. It is a **Financial and Timeline Reconciliation Gate** inserted between Parser Preview and Replay acceptance:

```text
Parsed Preview
   ↓
FinancialConsistencyService
   ↓
TimelineReconciliationService
   ↓
Historical Market Replay
   ↓
Owner Review
```

This gate should retain source-asserted outcomes, calculate market-derived outcomes independently, expose conflicts, and prevent contradictory results from entering Historical Reputation Summary.
