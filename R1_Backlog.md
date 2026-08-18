# R1 Trader Core Backlog

## هدف R1

تحويل CapitalGuard إلى أداة يومية موثوقة للمتداول الذي يستقبل توصية، يراجعها، يتابعها، يعرف ما فعّله فعليًا، ويتلقى التنبيه الصحيح ثم يحصل على تقرير قابل للتدقيق.

## R1-E1 — Unified Input

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E1-S1 | `/log` command | `interfaces/telegram/commands.py`, handler جديد | Unit/API/Telegram integration | direct text يمر بنفس Parser contract ولا ينشئ مسار parsing مختلف |
| R1-E1-S2 | Input source contract | `ParsingAttempt` وschemas | Contract test | source=`FORWARD` أو `DIRECT_INPUT` محفوظ ومميز |
| R1-E1-S3 | Parse normalization | `parsing_service.py` | Arabic/English/decimal cases | الأصول والأسعار والأهداف تتطبع إلى قيم موحدة |
| R1-E1-S4 | Validation | `creation_service.py` وdomain | Unit negative cases | رفض side غير صالح، SL في الجانب الخطأ، target غير منطقي، وأرقام غير finite |

## R1-E2 — Review and Confirmation

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E2-S1 | Review payload | `forward_parsing_handler.py` | Telegram integration | البطاقة تعرض asset/side/entry/SL/targets/market/source |
| R1-E2-S2 | Edit fields | callback handlers | E2E mocked Telegram | كل تعديل يعيد validation ولا يحفظ القيمة القديمة بالخطأ |
| R1-E2-S3 | Confirm idempotency | handler + Dedup | repeated callback integration | ضغط Confirm مرتين ينتج UserTrade واحدًا |
| R1-E2-S4 | Cancel/expire | session state | Unit/Integration | Cancel لا ينشئ DB record، وانتهاء الجلسة واضح للمستخدم |

## R1-E3 — Watchlist and Activation

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E3-S1 | Watchlist create | `CreationService`/`UserTrade` | DB integration | status=`WATCHLIST`، source channel محفوظ، لا PnL |
| R1-E3-S2 | Activation action | `create_trade_from_recommendation` وlifecycle | Integration | activation مسموح للمالك فقط وينقل إلى `ACTIVATED` |
| R1-E3-S3 | Activation event | `UserTradeEvent` | DB assertion | event يحتوي actor/time/reason/source |
| R1-E3-S4 | Duplicate prevention | `DedupLedgerService` | Unit/race integration | duplicate داخل 5m مرفوض، نافذة جديدة تسمح بإشارة جديدة |

## R1-E4 — Monitoring and Alerts

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E4-S1 | Trigger registration | `AlertService`, `PriceStreamer` | fake feed integration | trigger لا يسجل إلا Activated أو policy محددة |
| R1-E4-S2 | Symbol subscription | `price_streamer.py`, Redis events | integration | الرمز يضاف مرة واحدة ويزال بعد إغلاق آخر position |
| R1-E4-S3 | Target alert | alert strategy | deterministic test | كل target يرسل مرة واحدة ويحدث event |
| R1-E4-S4 | SL alert | lifecycle/alert | deterministic test | SL يرسل alert ويغلق أو يضع policy واضحة |
| R1-E4-S5 | Reconnect/degraded mode | `ws_client.py` | fault injection | انقطاع Binance لا يسبب crash ولا alerts وهمية |

## R1-E5 — Close and Reporting

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E5-S1 | Manual close | `LifecycleService` | integration/security | owner only، close idempotent، close price محفوظ |
| R1-E5-S2 | PnL | `lifecycle_service.py` وperformance | reference dataset | LONG/SHORT/zero/invalid cases بدقة معروفة |
| R1-E5-S3 | Activated-only query | `performance_service.py` | database/reference | Watchlist لا تدخل التقارير المالية |
| R1-E5-S4 | Holding time | performance service | unit/reference | الزمن بين Activated وClosed فقط |
| R1-E5-S5 | Report delivery | Telegram management handlers | API/Telegram smoke | تقرير واضح مع فترة ومصدر ووقت توليد |

## R1-E6 — Funnel and Quality

| Story | Task | Code | Test | Acceptance |
|---|---|---|---|---|
| R1-E6-S1 | Funnel events | audit/metrics service | integration | forward→parse→review→confirm→activate→close events قابلة للاستعلام |
| R1-E6-S2 | Product metrics | metrics module | smoke | activation rate وD7 retention source data متاحة دون PII غير ضروري |
| R1-E6-S3 | Regression suite | `tests/` | full pytest/CI | لا regression في Gate 0 ومسارات R1 |

## PR sequencing

1. PR-R1-01: `/log` وinput contract.  
2. PR-R1-02: validation/review/edit/confirm.  
3. PR-R1-03: Watchlist/Activation state and events.  
4. PR-R1-04: monitoring/alert deterministic tests.  
5. PR-R1-05: close/PnL/report reconciliation.  
6. PR-R1-06: funnel metrics وAlpha readiness.

لا يبدأ PR-R1-01 قبل `GO R1 Development`، ولا تُفتح Alpha قبل اكتمال PR-R1-05 وE2E Staging.
