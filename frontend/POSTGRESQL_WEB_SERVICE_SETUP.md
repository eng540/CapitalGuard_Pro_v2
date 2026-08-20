# CapitalGuard Web PostgreSQL Setup

## هدف هذه الخدمة

هذه القاعدة تخص **CapitalGuard Web SaaS فقط**. لا تستبدل ولا تشارك PostgreSQL التابعة لـ CapitalGuard Core. يحتفظ Core بملكية التوصيات والصفقات وPnL والتاريخ وOutbox، بينما تخزن هذه القاعدة الجلسات والتفضيلات والمسودات والمقارنات المحفوظة وسجل Web فقط.

## إعداد Railway

1. داخل مشروع Railway نفسه، أنشئ PostgreSQL جديدة باسم واضح مثل `capitalguard-web-postgres`.
2. أنشئ خدمة Web جديدة من المستودع، واضبط **Root Directory** إلى `frontend`.
3. في Variables لخدمة Web، أنشئ reference من رابط PostgreSQL الجديدة إلى `DATABASE_URL`.
4. لا تستخدم رابط PostgreSQL الذي تملكه خدمة Core، حتى لو كان في المشروع نفسه.
5. أنشئ `JWT_SECRET` فريدًا عبر `openssl rand -base64 48` وأضفه للخدمة فقط.
6. أضف `CAPITALGUARD_CORE_BASE_URL` ومفتاح Core **محدود القراءة** للخدمة فقط.

## دورة migrations

تحتوي `drizzle/0000_*.sql` على baseline PostgreSQL خاص بقاعدة Web. أثناء النشر يستدعي Railway:

```text
pnpm run db:migrate
node dist/index.js
```

لا يولد التطبيق migration عند التشغيل. أي تغيير مستقبلي يبدأ بتعديل schema ثم `pnpm run db:generate` ومراجعة SQL ودمجه في PR، ثم يطبقه `db:migrate` على PostgreSQL Web فقط.

## حدود الأمان

لا تضف مفاتيح Telegram أو البورصات إلى هذه الخدمة. لا تمنح `DATABASE_URL` الخاصة بالويب وصولًا إلى Core. كل قراءة مالية تمر من Web server إلى Core API، وكل mutation مالية مستقبلية تمر من Core API بعملية idempotent وسجل تدقيق.
