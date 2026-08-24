# G7-CON-01 — Canonical-to-Decision Handoff

## الغرض

يضيف هذا PR أول construction behavior فوق عقود الملكية: Application Use Case نقي يستقبل Canonical Semantic Representation المقبولة، يطبعها ويثبت سلامتها ويولد fingerprint قابلًا للتتبع، دون persistence أو Recommendation creation أو Execution.

## المسار

```text
G6 Semantic Canonical
→ OperationalDecisionService.prepare
→ READY_FOR_ANALYSIS
→ لاحقًا: explicit decision/recommendation command
```

## القيود

- لا يقبل المدخلات الناقصة أو غير الصالحة.
- يرفض `EXECUTION_STATE` لأن التنفيذ يحتاج command وحدودًا صريحة.
- لا يقبل AI أو Web payload بوصفه مصدرًا مباشرًا.
- لا ينشئ `HistoricalSignal` أو Recommendation أو UserTrade.
- لا يكتب إلى قاعدة البيانات ولا ينفذ network I/O.
- يثبت `decision_fingerprint` من canonical normalized input وevidence.
- يعيد استخدام `DecisionBoundary` من PR-G7-OWN-04.

## مثال العقد

```json
{
  "asset": "BTCUSDT",
  "direction": "LONG",
  "entry": "77000",
  "stop_loss": "76000",
  "targets": ["78000"],
  "market": "FUTURES"
}
```

النتيجة هي `READY_FOR_ANALYSIS` مع normalized canonical values وfingerprint. لا تعني النتيجة أن Recommendation أو Execution صار مسموحًا.

## نطاق PR

يشمل هذا PR خدمة application نقيّة واختبارات وتوثيقًا. لا يغير G5/G6 أو CreationService أو LifecycleService أو AutoTradeService أو API أو ORM أو migrations. نقل النتيجة إلى Recommendation أو UserTrade يحتاج command/use case مستقلًا ومراجعة منفصلة.
