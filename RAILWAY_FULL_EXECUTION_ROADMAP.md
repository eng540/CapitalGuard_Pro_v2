# CapitalGuard Full Execution Roadmap on Railway

## القرار التنفيذي

Railway هو بيئة التشغيل الفعلية، لكن لا يتم الدمج مع `main` أو إطلاق Alpha لمجرد أن الخدمة تعمل. كل مرحلة تمر بـ **Implementation → Tests → Evidence → Review → GO/NO-GO → Next Phase**.

## المرحلة 0 — Railway Foundation & Stabilization

**الهدف:** إثبات أن النسخة الحالية قابلة للبناء والهجرة والتشغيل والاستعادة على Railway.

| Workstream | مخرجات الإغلاق |
|---|---|
| Build | Dockerfile build ناجح، non-root، `PORT` ديناميكي |
| Migration | `entrypoint.sh` ينفذ migration مرة واحدة، PostgreSQL fresh/existing evidence |
| Health | `railway.toml` يضبط `/health` وrestart، smoke 200 بعد readiness |
| Secrets | Variables/Secrets كاملة بلا default أو Git leakage |
| Recovery | backup/restore وRTO/RPO |
| Runtime | Redis/Telegram/AI startup، logs، reconnect/degraded mode |
| Regression | pytest، compileall، Bandit High، pip-audit، Alembic heads |

**Gate 0:** `GO R1 Development` فقط بعد الأدلة أعلاه. `GO Alpha` يحتاج أيضًا E2E خارجي وrestore drill.

## R1 — Trader Core

**المدة المرجعية:** 4–6 أسابيع بعد Gate 0.  
**الهدف:** حلقة المتداول اليومية: `Forward /log → Parse → Validate → Review → Confirm → Watchlist → Activate → Monitor → Alert → Close → Report`.

| Sprint | Scope | Gate |
|---|---|---|
| R1-S1 | `/log` وinput contract وتوحيد Parser | contract tests |
| R1-S2 | Review/edit/confirm وstate transitions | E2E mocked Telegram |
| R1-S3 | Activation/ownership/events وDedup hardening | integration/security |
| R1-S4 | Price monitoring وSL/TP alerts وclose/PnL | deterministic feed |
| R1-S5 | Activated-only reports وfunnel metrics | reference dataset |

**Gate R1:** 20–50 مستخدم Alpha داخلي، لا duplicate، لا unauthorized ownership، التقارير متطابقة مع dataset مرجعي، p95 مقبول، وrollback معروف.

## Alpha — إثبات القيمة

**المدة:** أسبوعان. لا يوجد دفع أو تداول آلي. تُقاس Activation rate، D7 retention، Time-to-Value، Parser pass rate، alert latency، report correctness، وعدد الحوادث. أي خلل مالي أو PII يعيد الحالة إلى NO-GO.

## R2 — Trust & Analyst Discovery

**الهدف:** بناء ثقة قابلة للقياس في المحللين قبل السوق التجاري.

المخرجات: `AnalystProfile` public، `/find_analysts`، leaderboard قابل للتفسير، win rate، total PnL، drawdown، exposure، sample size، average holding، history، channel comparison، DedupLedger/analytics events، privacy controls، وبلاغات report correction.

**Gate R2:** metrics لا تعاقب المحلل بعينة صغيرة، لا تُعرض أرقام بلا فترة/عينة، moderation وreport correction متاحان، وجميع الإحصاءات مبنية على Activated/Closed فقط.

## R3 — Monetization Beta

**الهدف:** اشتراك محدود لا يفسد الثقة ولا يسمح بتجاوز الصلاحيات.

المخرجات: Plans، Entitlements، Payment Provider sandbox، signed webhook، idempotency، Subscription Ledger، Premium Guard، refunds، reconciliation، grace period، audit trail، وsupport playbook.

**Gate R3:** payment sandbox ناجح، duplicate webhook آمن، refund/reconciliation مثبتان، لا entitlement escalation، شروط الاستخدام والخصوصية والتحذير المالي جاهزة، وrollback لا يفسد subscription state.

## R4 — Platform Release

**الهدف:** نقل المنتج من Bot-centric إلى منصة قابلة للإدارة والتوسع.

المخرجات: Web Dashboard للمتداول والمحلل، Admin/Ops، versioned `/api/v1`، rate limits، API keys/service auth، tenant boundary، data export/delete، audit logs، feature flags، product analytics، وSLO dashboards.

**Gate R4:** tenant isolation tests، API contract tests، load test، error budget، incident response، وcanary deployment على Railway.

## R5 — Copy Trading Sandbox

**الهدف:** تنفيذ محاكاة آمنة قبل أي أموال حقيقية.

المخرجات: execution ledger، broker adapter، secret manager، risk limits، kill switch، reconciliation، idempotency، sandbox only، manual approval، audit trail، circuit breakers، وemergency rollback.

**Gate R5:** لا production money قبل security review مستقل، legal review، sandbox soak test، reconciliation zero-difference، وkill switch drill.

## Railway Delivery Model

كل تغيير يمر بفرع مستقل وPR. Railway يراقب Dockerfile و`railway.toml`. `entrypoint.sh` هو مصدر migration الوحيد، و`railway.toml` يضبط healthcheck/restart. بعد deploy يجرى:

```bash
bash scripts/railway_smoke.sh https://<railway-domain>
```

يُحفظ deployment id، commit SHA، migration head، health response، smoke output، logs، وقرار rollback. لا تُرسل أسرار Railway في GitHub أو المحادثة.

## دمج main

لا يُدمج فرع مع `main` إلا عندما تكون كل شروط PR خضراء، وتكون migration وrollback موثقة، وتكون نتيجة Gate مناسبة للنطاق. دمج Gate 0 لا يعني Alpha؛ دمج R1 لا يعني الدفع؛ دمج R3 لا يعني Copy Trading.

## مؤشرات الإدارة

| المرحلة | المؤشرات الحاكمة |
|---|---|
| Gate 0 | uptime، health، restore، RTO/RPO، error rate |
| R1 | activation، duplicate rate، alert latency، report correctness |
| Alpha | D7/D30 retention، Time-to-Value، support incidents |
| R2 | analyst discovery، sample-adjusted trust، correction rate |
| R3 | trial→paid، webhook failure، refund/reconciliation |
| R4 | p95، SLO، tenant incidents، API adoption |
| R5 | execution correctness، reconciliation، kill-switch time |

## الوضع الحالي

الفرع الحالي يحتوي أساس Gate 0 محليًا وملفات Railway/CI/smoke الجديدة، لكنه لم يُختبر ضد Railway الفعلي من داخل هذه الجلسة لعدم وجود URL أو موصل Railway أو أسرار Staging. لذلك قرار الدمج مع `main` يبقى مشروطًا بنتائج PR والفحوص، وقرار Alpha يبقى NO-GO حتى استكمال الأدلة الخارجية.
