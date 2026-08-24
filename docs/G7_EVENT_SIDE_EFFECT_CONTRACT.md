# G7 Event and Side-Effect Ownership Contract — PR-G7-OWN-03

## الغرض

يثبت هذا العقد مالك كل عائلة أحداث ومسار الآثار الجانبية المرتبطة بها. لا يضيف هذا PR event bus أو persistence model جديدًا، ولا يغير نماذج الأحداث الحالية؛ إنه يضيف خريطة ownership قابلة للاختبار كي لا تتحول projections أو monitoring أو Web إلى مصدر حقيقة.

## الملكية

| عائلة الحدث | Aggregate | المالك |
|---|---|---|
| `RECOMMENDATION_LIFECYCLE` | Recommendation | `LifecycleService` |
| `USER_TRADE_LIFECYCLE` | UserTrade | `LifecycleService` |
| `HISTORICAL_REPLAY` | ReplayRun | `HistoricalMarketReplayService` |
| `PUBLICATION_DELIVERY` | PublicationDelivery | `PublicationOutboxService` |
| `WEB_COMMAND` | Command | `WebCommandService` |
| `LIVE_EXECUTION` | Execution | `AutoTradeService` |
| `MONITORING_ACTION` | Recommendation | `AlertService` |

## هوية الحدث

ينبغي أن ترتبط كل عائلة بهوية قابلة لإعادة المحاولة والمراجعة. يحدد العقد identity الأولي لكل عائلة؛ وعند تعديل runtime behavior في PR لاحق يجب توحيد `aggregate_id` و`event_type` و`actor` و`causation_id` و`correlation_id` و`occurred_at` و`version` وpayload schema.

## قاعدة الآثار الجانبية

```text
Command owner records intent
Aggregate owner changes state
Event owner records fact
Worker/projection consumes fact
External side effect is retried independently
```

لذلك لا يكتب StrategyEngine أو read model أو Trust projection على مصدر الحقيقة. AlertService يطلب lifecycle action، وOutbox يملك delivery retry، وAutoTradeService لا يعمل إلا خلف execution gates.

## نطاق PR

يشمل هذا PR contract وtests وdocumentation فقط، ولا يغير event models أو migrations أو services التشغيلية أو API أو ranking/trust behavior. أي توحيد فعلي للحقول أو نقل event creation يحتاج PR سلوكي مستقلًا ومراجعة code-versus-report.
