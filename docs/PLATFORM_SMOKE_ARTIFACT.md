# Platform Smoke Artifact

هذا artifact تشغيلي غير مالي يعيد التحقق من جاهزية خدمتي **Core** و**Web** ومن حدود API العامة في Core. لا يرسل أوامر تداول، ولا يستخدم بيانات Telegram أو مفاتيح خدمية، ولا يقرأ محفظة أو توصية أو أي بيانات مالية.

## نطاق الفحص

| الفحص | العقد المقبول |
|---|---|
| Core health | `{"status":"ok"}` |
| Web health | `{"status":"ok","service":"capitalguard-web"}` |
| Core API v1 | `api_version=v1` و`service=capitalguard-core` و`status=ok` و`commercial_mode=noncommercial` |

## التشغيل

```bash
bash scripts/platform_smoke.sh https://core.example https://web.example
```

للتشغيل المؤتمت في GitHub Actions، يمرر workflow العناوين العامة فقط كـinputs. يجب حفظ مخرجات النجاح أو الفشل مع timestamp في سجل الإصدار، من دون حفظ أسرار أو استجابات مالية.

> نجاح هذا الفحص دليل جاهزية سطحية للعقود العامة فقط. لا يحل محل Fresh Migration أو reconciliation أو اختبار Telegram E2E أو load/SLO أو أي بوابة تجارية.
