# Fresh PostgreSQL Migration Evidence

تغلق هذه الآلية جزء **Fresh PostgreSQL migration** من G0، عبر خدمة PostgreSQL مؤقتة ومعزولة في CI. لا تتصل بـRailway، ولا تستورد snapshot، ولا تتعامل مع أي بيانات إنتاج.

## ما يثبته job

| خطوة | المعيار |
|---|---|
| قاعدة جديدة | PostgreSQL 16 بخدمة CI مؤقتة |
| ترحيل | `PYTHONPATH=src alembic upgrade head` ينجح |
| schema | وجود `users` و`recommendations` و`user_trades` و`alembic_version` |
| head | قيمة `alembic_version` غير فارغة |
| خلو البيانات | counts للجداول المالية الأساسية تساوي صفر |

> لا يثبت هذا artifact **Existing-data reconciliation**؛ فذلك يتطلب snapshot مقنّعاً في بيئة معزولة مع عدادات وحالات وعلاقات مرجعية. كما لا يثبت load/SLO أو Telegram E2E أو أي جاهزية تجارية.
