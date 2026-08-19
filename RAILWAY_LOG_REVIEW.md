# Railway Log Review — 2026-08-19

## Evidence source

Owner-provided Railway log attachment `logs.1787098014312.log.txt`.

## Verified

| Evidence | Log lines | Result |
|---|---:|---|
| Container start | 1 | PASS |
| PostgreSQL Alembic context | 3–4 | PASS; `PostgresqlImpl` with transactional DDL |
| Migration completion | 5 | PASS |
| Supervisor/API process | 6–8 | PASS |
| Application startup | 9–10, 22 | PASS |
| Uvicorn listener | 23 | PASS; `0.0.0.0:8080` |
| Telegram webhook traffic | 24–38, 48, 54–77 | PASS; repeated HTTP 200 |
| WebApp price/create | 39–53 | PASS/PARTIAL |
| Valid Telegram WebApp request | 51, 53 | PASS |
| External `/health` and `/metrics` | Smoke run | PASS |
| Invalid Telegram initData | Smoke run and 83–85 | Rejected |
| TradingView without secret | Smoke run and 89–90 | HTTP 401 |

## Findings

The log contains PTB warnings on multiple `ConversationHandler` instances because `per_message=False` is used with callback handlers (lines 12–21). These warnings do not prevent startup but require an R1 follow-up decision per conversation.

Requests without Telegram hash produce `Auth Error: No hash found` at error level (lines 41 and 45–47). The request is rejected, but the logging level should be reduced or rate-limited to avoid alert noise. Some unauthorized WebApp requests return HTTP 200 with an error body (lines 42 and 46); the API contract should decide whether to preserve this WebApp behavior or return 401/403 consistently.

`favicon.ico` returns 404 (lines 43, 52, and 79), which is cosmetic. The log does not expose migration head, backup timestamp, restore result, RTO, RPO, or existing-data reconciliation.

## Decision impact

The evidence supports a conditional merge of Railway hardening PR 179. It does not authorize Alpha, public launch, payments, or Copy Trading until recovery and data-reconciliation evidence is available.
