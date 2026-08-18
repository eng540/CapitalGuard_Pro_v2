# Requirement Traceability Matrix

## مفتاح الحالة

`VERIFIED` = التنفيذ مثبت بالكود والاختبار.  
`PARTIAL` = جزء من السلوك موجود أو الاختبار غير مكتمل.  
`EXISTS — REQUIRES FIX` = موجود لكنه غير جاهز تشغيليًا.  
`NOT FOUND` = لم يوجد دليل كافٍ في الفرع الحالي.

| ID | Requirement | Feature/Task | Code | Data | Test | Evidence | Status |
|---|---|---|---|---|---|---|---|
| TR-001 | استقبال Forwarded signal | Parser input | `interfaces/telegram/forward_parsing_handler.py` | `parsing_attempts` | `tests/test_parsing.py` + integration | `pytest_gate0_final_v2.txt` | VERIFIED |
| TR-002 | دعم الأرقام العربية واللواحق | Parser normalization | `application/services/parsing_service.py` | ParsingAttempt | parser unit tests | parser test log | VERIFIED |
| TR-003 | التحقق من LONG/SHORT وSL/TP | Validation contract | `creation_service.py` | `recommendations/user_trades` | trade service tests | `pytest_trade_after_fixes_v4.txt` | VERIFIED |
| TR-004 | Review قبل الحفظ | Review/Confirm | `forward_parsing_handler.py` | attempt/session state | Telegram integration | مطلوب Staging evidence | PARTIAL |
| TR-005 | إنشاء Watchlist | Forward create | `creation_service.py` | `user_trades`, `watched_channels` | integration | `pytest_integration_after_dedup.txt` | VERIFIED جزئيًا |
| TR-006 | منع تكرار Forward | DedupLedger | `dedup_service.py`, `creation_service.py` | `dedup_ledger` | `test_dedup_ledger.py` + E2E | `pytest_dedup_savepoint.txt` | VERIFIED |
| TR-007 | Activate من توصية | Recommendation tracking | `creation_service.py` | `user_trades` | integration flow | test output | VERIFIED جزئيًا |
| TR-008 | Price monitoring | Alerts | `alert_service.py`, `price_streamer.py` | trigger state/Redis | fake feed integration | مطلوب external evidence | PARTIAL |
| TR-009 | SL/TP alert | Alert lifecycle | `alert_service.py`, `lifecycle_service.py` | `user_trade_events` | integration | مطلوب Staging evidence | PARTIAL |
| TR-010 | إغلاق الصفقة وحساب PnL | Close | `lifecycle_service.py` | `user_trades`, events | `test_trade_service.py` | full pytest | VERIFIED |
| TR-011 | تقرير Activated-only | Reporting | `performance_service.py`, `analytics_service.py` | `user_trades` | reference dataset | مطلوب reconciliation | PARTIAL |
| TR-012 | `/log` direct input | Trader R1 | NOT FOUND | يحتاج parsing attempt source | API/Telegram E2E | لا يوجد | NOT FOUND |
| TR-013 | Public analyst discovery | `/find_analysts` | NOT FOUND | `analyst_profiles/stats` موجودة جزئيًا | API integration | لا يوجد | NOT FOUND |
| TR-014 | Analyst reputation | Stats/ranking | `analytics_service.py` جزئيًا | `analyst_stats` | reference dataset | لا يوجد leaderboard evidence | PARTIAL |
| TR-015 | Payment/subscription | Monetization | NOT FOUND | `subscriptions` ORM فقط | payment sandbox | لا يوجد | NOT FOUND |
| TR-016 | Premium entitlement | Entitlements | NOT FOUND | يحتاج ledger | API/security | لا يوجد | NOT FOUND |
| TR-017 | Admin Dashboard | Operations | NOT FOUND | يحتاج audit/admin views | API/UI E2E | لا يوجد | NOT FOUND |
| TR-018 | Versioned Public API | Platform | NOT FOUND | API contracts | OpenAPI/contract | لا يوجد | NOT FOUND |
| TR-019 | Tenant isolation | Platform | PARTIAL | لا يوجد tenant_id شامل | security integration | لا يوجد | NOT FOUND |
| TR-020 | Copy Trading Sandbox | R5 | NOT READY | يحتاج execution ledger/secrets | sandbox E2E | لا يوجد | NOT FOUND |
| TR-021 | Health/readiness | Operations | `interfaces/api/main.py` | service state | `tests/test_api.py` | `pytest_gate0_final_v2.txt` | VERIFIED جزئيًا |
| TR-022 | Secret fail-closed | Security | `config.py`, `auth.py`, `tradingview.py` | env/secrets | security tests | Bandit/pip-audit | VERIFIED |
| TR-023 | Backup/Restore | Recovery | `backup_service.py` | DB/backup storage | restore drill | غير منفذ خارجيًا | PARTIAL |
| TR-024 | Fresh migration | Database | `alembic/versions/*` | schema | PostgreSQL empty DB | SQLite blocked by old baseline | PARTIAL |

## فجوات التتبع الحرجة

المتطلبات `TR-004` و`TR-008` و`TR-009` و`TR-011` و`TR-023` و`TR-024` تحتاج أدلة Staging، بينما `TR-012` وما بعده في نطاق R1/R2/R3 لم تُنفذ بعد. لا يجوز استخدام وجود ORM أو اسم خدمة كدليل على اكتمال المتطلب.

## قاعدة قبول التتبع

لا تُغلق المتطلبات التجارية إلا عند وجود: رابط كود، migration أو query عند الحاجة، اختبار مناسب، سجل تشغيل أو artifact، وPR يمكن الرجوع إليه. المتطلبات `NOT FOUND` تبقى خارج الإطلاق ولا تُعرض في التسويق كميزات متاحة.
