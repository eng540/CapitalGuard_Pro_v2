# Test Matrix

## 1. مستويات الاختبار

| النوع | الهدف | متى يستخدم |
|---|---|---|
| Unit | عقد الدالة وbusiness rules | Parser، validation، PnL، fingerprint |
| Integration | خدمة + DB/Redis fake | lifecycle، repositories، Dedup |
| API | HTTP contract وauth | health، webapp، webhook |
| Database | schema/FK/migration/query | Alembic، reports، indexes |
| Security | negative access/secrets/replay | RBAC، webhook، PII |
| E2E | رحلة المستخدم الكاملة | Forward→Close |
| Regression | منع عودة عيب مصحح | كل PR متعلق بعقد سابق |
| Smoke | بعد deployment | startup، health، critical paths |
| Load/Failure | latency/reconnect/backpressure | بعد R1 وقبل Alpha |

## 2. مصفوفة المتطلبات

| ID | Area | Unit | Integration | API | DB | Security | E2E | Smoke | Current evidence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| T-01 | Arabic parser | نعم | نعم | لا | لا | لا | نعم | نعم | `tests/test_parsing.py` |
| T-02 | Validation | نعم | نعم | لا | لا | لا | نعم | لا | `tests/test_trade_service.py` |
| T-03 | State transitions | نعم | نعم | لا | نعم | نعم | نعم | لا | جزئي |
| T-04 | Dedup fingerprint/window | نعم | نعم | لا | نعم | نعم | نعم | لا | `test_dedup_ledger.py` |
| T-05 | Review/Confirm | جزئي | نعم | لا | لا | نعم | مطلوب | مطلوب | لا evidence خارجي |
| T-06 | Health/readiness | لا | جزئي | نعم | لا | نعم | لا | نعم | `tests/test_api.py` |
| T-07 | TradingView webhook | لا | نعم | نعم | لا | نعم | نعم | نعم | unit/needs staging |
| T-08 | Alert target/SL | نعم | نعم | لا | نعم | جزئي | نعم | نعم | needs fake feed |
| T-09 | Close/PnL | نعم | نعم | لا | نعم | ownership | نعم | نعم | `test_trade_service.py` |
| T-10 | Reports | نعم | نعم | لا | نعم | PII | نعم | نعم | needs reference dataset |
| T-11 | Backup/Restore | لا | نعم | لا | نعم | secrets | نعم | نعم | NOT VERIFIED |
| T-12 | `/log` | نعم | نعم | نعم | نعم | auth | نعم | نعم | NOT FOUND |
| T-13 | Analyst discovery | نعم | نعم | نعم | نعم | RBAC | نعم | نعم | NOT FOUND |
| T-14 | Payments | نعم | نعم | نعم | نعم | webhook/fraud | نعم | نعم | NOT FOUND |
| T-15 | Tenant isolation | نعم | نعم | نعم | نعم | critical | نعم | نعم | NOT FOUND |
| T-16 | Copy Trading | نعم | نعم | نعم | نعم | critical | نعم | نعم | NOT READY |

## 3. Gate 0 execution set

يجب تنفيذ مجموعة `G0` التالية على كل release candidate: `pytest -q`، Parser/Trade/Dedup integration، API smoke، `compileall`، Bandit High، pip-audit، `alembic heads`، PostgreSQL fresh upgrade، existing-data upgrade، restore drill، Redis/Telegram startup، وE2E Forward-to-Close.

## 4. Test data rules

يجب أن تتضمن datasets حالات LONG وSHORT، target واحد ومتعدد، أرقام عربية، duplicate source text، channel مختلف، Watchlist غير مفعلة، Activated، Partial close، full close، missing price، reconnect، unauthorized owner، والـ PII redaction. لا يستخدم الاختبار بيانات مستخدم حقيقية.

## 5. Evidence format

كل اختبار Gate يحفظ command، commit، environment class، timestamp، exit code، summary، وartifact path. اختبارات Staging تحفظ أيضًا service version وmigration head وhealth output وrollback reference.
