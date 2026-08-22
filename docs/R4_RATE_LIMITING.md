# R4 Public API Rate Limiting

تطبق هذه الخطوة حارس burst محلياً على `GET /api/v1/status` بمعدل **60 طلباً لكل عنوان عميل في الدقيقة**. المسار لا يحتوي على بيانات مالية أو أوامر، ويظل الحارس مقيداً به عمداً.

| داخل النطاق | خارج النطاق عمداً |
|---|---|
| عقد الحالة العامة `/api/v1/status` | Telegram webhook |
| رد `429` مع `Retry-After` عند تجاوز الحد | Web server-to-server Read Models |
| حدّ burst داخل عملية Core | أوامر UserTrade/Analyst التي تملك idempotency وCore authorization خاصين |

> هذا حارس per-process دفاعي ولا يدّعي أنه بديل عن rate limiting موزع عند الحافة. لا يثق برؤوس forwarded client القابلة للتزوير، ولا يغير مسارات Telegram أو Web الخادمية. سيظل تصميم distributed limit عبر Redis/edge مسار R4 منفصلاً عند اعتماد traffic/SLO envelope.
