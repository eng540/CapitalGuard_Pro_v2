# CapitalGuard Gate Scorecards

**قاعدة الاستخدام:** لا تحل علامة `BUILD_DONE` محل الدليل الخارجي. تعني `PASS` أن الدليل موجود ومراجع، وتعني `OPEN` أن العمل لا يزال مطلوباً، وتعني `HOLD` أن التفعيل ممنوع عمداً.

## G0 — Foundation & Stabilization

| بند | الحالة | الدليل/العمل التالي |
|---|---|---|
| CI وcompileall وBandit وpip-audit | PASS جزئي | نجاحات موثقة في PRs وCI؛ يعاد تشغيلها لكل release candidate. |
| Health/readiness وsmoke | PASS جزئي | Railway health موجود؛ يلزم artifact موحد للـ smoke. |
| Fresh PostgreSQL migration | OPEN | تشغيل على قاعدة فارغة وتسجيل head. |
| Existing-data reconciliation | OPEN | restore anonymized snapshot وعدّ counts/FKs/statuses. |
| Backup/Restore وRTO/RPO | PASS | Restore Drill منفصل ومقنّع بتاريخ 21 أغسطس 2026: Core/Web restore وAlembic/Drizzle وcounts ناجحة؛ RTO ≈45 ثانية، وRPO <5 دقائق. راجع `docs/RESTORE_DRILL_EVIDENCE_20260821.md`. |
| E2E Forward→Close حي | OPEN | سيناريو موثق مع Redis/Telegram/market controlled inputs. |
| أسرار وPII | OPEN | دليل تدوير مقنّع وretention/access register. |

## R1 وAlpha/Value

| بند | الحالة | الدليل/العمل التالي |
|---|---|---|
| `/log`، lifecycle، PnL، history، dedup | BUILD_DONE | PR #181 و#183–#186 واختبارات السلوك. |
| alerts/outbox notifications | BUILD_DONE جزئياً | PR #189–#190 و#195؛ يلزم UAT systematic. |
| Activated-only reconciliation | OPEN | dataset مرجعي ونتيجة ≥99%. |
| p95 للأوامر والتقارير | OPEN | load/latency artifact. |
| Alpha cohort | OPEN | allow-list 20–50 مستخدماً وincident/support register. |
| Value metrics | OPEN | D7/activation/TTFV حسب metrics contract. |

## R2 وHistorical Trust

| بند | الحالة | الدليل/العمل التالي |
|---|---|---|
| Analyst profile/discovery/comparison | BUILD_DONE | PR #192–#197. |
| قبول محلل حي | OPEN | analyst account acceptance checklist. |
| رصد 24–48 ساعة | OPEN | outbox/divergence/support/retention evidence. |
| Historical import/replay/review | BUILD_DONE | PR #199–#218. |
| Historical real acceptance | OPEN | batch حقيقي + OHLCV + claim/review/replay evidence. |

## R3-C وR4 وR5-C

| البوابة | الحالة | الشرط التالي |
|---|---|---|
| R3-C Monetization | HOLD | قرار قانوني/تسعير/دعم ثم provider sandbox/webhook/refund/reconciliation. |
| R4 Platform | BUILD_DONE جزئياً / OPEN | أُسست ركيزة `/api/v1/status` غير المالية؛ تبقى tenant/rate limits/contract coverage/load/SLO/error budget/canary مفتوحة. |
| R5-C Copy Trading Sandbox | HOLD / NOT_STARTED | لا يبدأ قبل إغلاق R4 وقرار مستقل؛ C0–C5 تشمل sandbox, idempotency, reconciliation, kill switch, security/legal. |

### TG-01 — Recommendation identity/read security foundation

| البند | الحالة | الدليل/العمل التالي |
|---|---|---|
| UserTrade read model يعلن `entity_type` و`public_ref` و`display_ref` و`source` | BUILD_DONE | `webapp.py` و`tests/test_webapp_recommendation_contract.py`. |
| تفاصيل UserTrade تستخدم public ref وتفرض owner scope داخل Core | BUILD_DONE | lookup مقيد بـ`user_id + public_ref` واختبار contract. |
| Web لا يعتمد على numeric id لمسار التفاصيل الجديد | BUILD_DONE | مسار details الجديد؛ `id` بقي مؤقتاً لتوافق القائمة القديمة فقط. |
| UAT لجلسة Telegram وقراءة تفاصيل trader واقعية | OPEN | يتطلب جلسة Telegram حية وحسابين للاختبار. |
| أوامر close/partial/SL/entry مميزة حسب entity ومحمية | NOT_STARTED | نطاق TG-04؛ المسار القديم لا يعتمد كعقد Web جديد. |

### TG-03 — Web read-only recommendation UI

| البند | الحالة | الدليل/العمل التالي |
|---|---|---|
| Adapter خادمي يطلب القائمة والتفاصيل من Core service-to-service | BUILD_DONE | `frontend/server/core-adapter.ts` واختبارات Vitest. |
| tRPC يشتق Telegram ID من جلسة Web ولا يقبل هوية client | BUILD_DONE | `frontend/server/capitalguard.ts`. |
| صفحة تفاصيل تقرأ public ref فقط ولا تعرض mutation action | BUILD_DONE | `frontend/client/src/pages/Recommendations.tsx`. |
| UAT لجلسة Telegram حقيقية وملكية التفاصيل | OPEN | حسابان اختباريان ومرجع public مملوك/غير مملوك. |

### TG-04 — UserTrade command foundation

| البند | الحالة | الدليل/العمل التالي |
|---|---|---|
| إغلاق المسار الرقمي المختلط | BUILD_DONE | `POST /api/webapp/action` يعيد 410. |
| Close command بالـpublic ref وowner scope والسعر الخادمي | BUILD_DONE | `WebCommandService.close_user_trade`. |
| idempotency وaudit generic للهدف | BUILD_DONE | `WebCommandAudit` fingerprint/target type. |
| Web action وUAT حي | OPEN | لا زر قبل سيناريو close/replay/ownership/unavailable-price. |

## قواعد القرار

لا يجوز تغيير `OPEN` إلى `PASS` دون رابط PR أو commit، أمر اختبار، بيئة، timestamp، نتيجة، ومالك قرار. ولا يزيل بدء نافذة رصد R5 أي سبب HOLD خاص بالاستعادة أو القرار التجاري أو Copy Trading.

## سجل قرارات التشغيل

| التاريخ | القرار | النطاق المسموح | الأثر المحظور أو المؤجل |
|---|---|---|---|
| 21 أغسطس 2026 | تأجيل Restore Drill | استمرار تطوير المنتج غير التجاري، الترقيات، الاختبارات المحلية، UAT غير التجاري، ودمج التحسينات عبر PRs. | لا اعتماد تجاري، لا تفعيل دفع، لا تغيير R5 من HOLD، ولا Copy Trading قبل Restore Drill منفصل وموثق. |
| 21 أغسطس 2026 | إغلاق دليل Restore Drill | دليل restore منطقي منفصل لـ Core وWeb مع migrations/integrity وRTO/RPO مقنّعة. | يبقى الدفع وCopy Trading والتنفيذ الحي محظوراً؛ لا تزال بقية أدلة G0/R4 وقرارات R3-C/R5-C مستقلة ومفتوحة. |
