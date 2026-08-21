# CapitalGuard Metrics Contract

**الإصدار:** 1.0  
**الحالة:** حاكم لـ Sprint G-1؛ لا يُستخدم لادعاءات تجارية قبل تشغيل مصالحة قبول موثقة.

## 1. مبادئ الحساب

كل timestamps تخزن UTC. Core هو المصدر الوحيد للحقيقة المالية، ويعرض Web القيم قراءةً فقط. لا يدخل سجل `WATCHLIST` في أي PnL أو Win Rate أو Profit Factor أو Drawdown أو نتائج محلل. يجب أن يظهر كل مقياس بالفترة ووقت التوليد وحجم العينة وإصدار هذا العقد.

| المصطلح | التعريف الحاكم |
|---|---|
| Activated trade | سجل متداول انتقل إلى `ACTIVATED` بموافقة المالك قبل الإغلاق. |
| Closed activated trade | Activated trade له حدث إغلاق موثوق وحالة `CLOSED`. |
| Watchlist | مراقبة فقط؛ لا تدخل أي مقياس مالي. |
| Historical verified event | حدث تاريخي يملك source/time/market evidence ويحمل replay status موثق؛ يبقى منفصلاً عن live analytics. |
| Sample size | عدد السجلات المطابقة للفلتر بعد تطبيق policy المقياس، لا عدد الرسائل أو المشاهدات. |

## 2. مقاييس الأداء

| المقياس | الصيغة | المجموعة | الاستثناءات |
|---|---|---|---|
| Win Rate | `closed profitable / closed activated × 100` | Closed activated فقط | لا يظهر دون sample size والفترة. |
| Total PnL % | مجموع `pnl_percentage` للسجلات المؤهلة | Closed activated فقط | لا يخلط PnL التاريخي أو Watchlist. |
| Profit Factor | مجموع الربح الموجب ÷ القيمة المطلقة لمجموع الخسارة السالبة | Closed activated فقط | `N/A` عند غياب خسائر أو صفقات مغلقة كافية. |
| Average Holding | متوسط `closed_at - activated_at` | Closed activated فقط | يستبعد timestamps الناقصة أو المتناقضة. |
| Max Drawdown | أكبر هبوط من قمة منحنى PnL تراكمي مرتب زمنياً | Closed activated فقط | لا يصدر رسمياً قبل dataset reconciliation. |
| Exposure | حصة رأس المال أو الصفقات النشطة حسب الأصل/القناة/الفترة | Activated فقط | يحتاج تعريف capital basis في التقرير. |

## 3. مقاييس funnel والاحتفاظ

| المقياس | البسط | المقام | المصدر |
|---|---|---|---|
| Parse pass rate | محاولات parsing الصالحة التي أنتجت payload قابل للمراجعة | محاولات parsing الصالحة | Core parsing events؛ يستبعد الرسائل الحرة غير القابلة للتحليل. |
| Confirm rate | عمليات confirm الناجحة | بطاقات review المعروضة | Core lifecycle/audit events. |
| Activation rate D7 | مستخدمون فعّلوا trade خلال 7 أيام | مستخدمون أنشأوا Watchlist صالحة | Core events؛ UTC cohort. |
| Time to first valid trade | `first_valid_trade_at - user_first_seen_at` | المستخدمون الجدد | يعرض median وp95، لا المتوسط وحده. |
| D7 retention | مستخدمون نشطون في اليوم 7 ± نافذة محددة | cohort المستخدمين الجدد | تعريف activity هو event منتجي موثق، لا مجرد request آلي. |

## 4. الأداء التشغيلي والحدود

| المقياس | الهدف الأولي | نقطة القياس | الإجراء عند التجاوز |
|---|---:|---|---|
| إدخال → ظهور | p95 ≤ 2s | Core timestamp إلى response/visible event | تشخيص Parser/queue/DB؛ لا ادعاء Alpha. |
| Watchlist → Activated | p95 ≤ 1s | command received إلى commit/event | فحص locks/transactions. |
| التقرير الأساسي | p95 ≤ 3s | Core server request | degraded/read-only response حسب Runbook. |
| Report reconciliation | ≥ 99% | dataset مرجعي معلوم | تجميد ranking/claims وإصلاح الحساب. |
| مؤثر duplicate | 0 | Dedup/lifecycle audit | عزل المسار وفتح incident. |

## 5. بيانات العرض والتاريخ

تعرض بيانات التاريخ في `HistoricalReputationSummary` منفصلة عن `AnalystStats` الحية. لا يصبح التاريخ eligible_for_ranking إلا بعد ownership وreplay والثقة المحددة في مسار H1–H8. لا يحق للويب إعادة حساب هذه القيم أو تخزينها كحقيقة مالية.

## 6. اختبارات العقد

أي تعديل على هذا العقد يضيف اختبارات للـ LONG/SHORT، partial/full close، Watchlist exclusion، sample window، ownership، timezone، ودقة التقريب. ويجب أن تذكر نتيجة الاختبار `metrics_contract_version=1.0` أو الإصدار اللاحق.
