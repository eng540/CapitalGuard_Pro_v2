# G7 Ownership Contract — PR-G7-OWN-01

## الغرض

يعرّف هذا العقد المالك الوحيد لكل مسؤولية مركزية في النظام. لا يضيف قاعدة بيانات أو aggregate أو orchestrator جديدًا، ولا يغير السلوك التشغيلي؛ بل يحول خريطة الملكية المعتمدة إلى contract نقي داخل `capitalguard.domain.ownership` قابل للاختبار.

## قواعد الملكية

| المسؤولية | المالك |
|---|---|
| Source receipt/revision | `HistoricalForwardingService` / `HistoricalMessageFoundationService` |
| Semantic materialization | `HistoricalSemanticMaterializationService` |
| Semantic acceptance | `HistoricalAdjudicationService` |
| HistoricalSignal | `HistoricalSignalMaterializationService` |
| Replay/Market Evidence | `HistoricalMarketReplayService` |
| Recommendation creation | `CreationService` |
| Lifecycle transition | `LifecycleService` |
| Monitoring/action routing | `AlertService` + `StrategyEngine` |
| Publication delivery | `PublicationOutboxService` |
| Typed command authorization | `WebCommandService` |
| Risk sizing/validation | `RiskService` + `CreationService` validation |
| Live execution | `AutoTradeService` + Binance executors |
| Performance read model | `PerformanceService` / `PerformanceRepository` |
| Historical trust release | `HistoricalReputationService` / `HistoricalTrustReleaseService` |

## Invariants

كل مسؤولية لها owner واحد غير فارغ، ونطاق كتابة صريح، ونطاق آثار جانبية صريح. read models لا تكتب على Source Truth، وAI لا يملك Semantic Truth أو Recommendation أو Execution State، وTrust/Ranking لا يفعّلان commerce أو execution.

## حدود PR

هذا PR يضيف contract وtests وdocumentation فقط. لا يغير G5 أو G6 Replay أو schema أو migrations أو API أو runtime wiring، ولا ينقل المسؤوليات فعليًا بين الخدمات. نقل الملكية أو تعديل behavior يحتاج PR لاحقًا ومراجعة مستقلة.
