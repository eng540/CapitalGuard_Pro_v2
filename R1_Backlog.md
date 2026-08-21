# R1 Trader Core Backlog — Status Register

## هدف R1

يبقى هدف R1 أن يدير المتداول دورة: إدخال → مراجعة → Watchlist → Activate → تنبيه → إغلاق → تقرير، مع استبعاد Watchlist من الأرقام المالية.

| Epic | البناء | دليل PR | قبول متبقٍ |
|---|---|---|---|
| Unified input (`/log`, forwarding, normalization) | BUILD_DONE | #181, #183 | UAT Telegram موثق |
| Review, confirm, ownership, dedup | BUILD_DONE | #181, #184, #188 | confirm/ownership edge UAT |
| Watchlist/Activated/events | BUILD_DONE | #181, #184 | report reconciliation |
| Monitoring/alerts/reconnect | BUILD_DONE جزئياً | #184, #190, #195 | p95/reconnect/fault evidence |
| Close/PnL/history | BUILD_DONE | #185, #186 | reference dataset ≥99% |
| Funnel/Alpha readiness | BUILD_DONE جزئياً | #181 | cohort/D7/TTFV/support evidence |

## Gate R1

لا تعتبر R1 مغلقة إلا عندما يثبت UAT أن رحلة متداول صحيحة، ولا يظهر duplicate مؤثر، وتتطابق التقارير مع dataset مرجعي، وتقاس p95، ولا يوجد P0/P1 في الإدخال أو التنبيه أو الإغلاق. لا يفتح ذلك الدفع أو Copy Trading.
