# Ox Alpha / OpenRouter Change Record

## Scope

Implemented the first cautious integration step for Ox Alpha inside the existing `ai_service` boundary. Core, Telegram, frontend Smart Analysis, database schemas, and business logic were not changed.

## Changes

| File | Change | Reason |
|---|---|---|
| `ai_service/services/llm_parser.py` | Added explicit `LLM_PROVIDER` handling for `openrouter`; added a dedicated OpenRouter payload helper; required key, full URL, and model before an upstream call. | Use the existing OpenAI-compatible request path without introducing a new provider framework, while failing closed on incomplete configuration. |
| `ai_service/services/image_parser.py` | Kept the existing explicit OpenRouter vision branch and changed unknown providers to fail closed instead of silently using an OpenRouter-shaped request. | Prevent ambiguous or misconfigured provider values from sending credentials to an unintended endpoint. |
| `.env.example` | Corrected the documented OpenRouter endpoint to `https://openrouter.ai/api/v1/chat/completions` and added `stealth/ox-alpha` with a JSON-object/strict-schema caveat. | Align deployment guidance with the verified OpenRouter API endpoint and Ox Alpha capability boundary. |
| `tests/test_ox_alpha_integration.py` | Added four local contract tests for text, vision, financial validation failure, and unknown-provider fail-closed behavior. | Prove request shape and safety behavior without sending secrets or traffic to a live provider. |
| `OX_ALPHA_CHANGE_RECORD.md` | Added this record. | Make scope and verification reproducible. |

## Preserved Contracts

The AI service response contract remains `ParseResponse` with `status`, optional `data`, `parser_path_used`, and `error`. The structured fields remain `asset`, `side`, `entry`, `stop_loss`, `targets`, and existing optional fields. No Core or Telegram consumer changes were required.

Ox Alpha requests use `response_format: {"type": "json_object"}`. Strict JSON Schema is intentionally not requested because the verified model metadata does not guarantee strict JSON-schema enforcement. Existing JSON extraction and financial consistency validation remain the final gate.

## Configuration

```dotenv
LLM_PROVIDER=openrouter
LLM_API_KEY=<OpenRouter secret>
LLM_API_URL=https://openrouter.ai/api/v1/chat/completions
LLM_MODEL=stealth/ox-alpha
```

The secret must be provided only through the AI service deployment environment. It must not be committed, exposed to the browser, or copied into test fixtures.

## Verification

| Check | Result |
|---|---:|
| Ox Alpha contract tests | 4 passed |
| Existing impacted parser/image tests | 38 passed, 1 skipped (pre-existing correction test requiring UOW setup) |
| Full Python suite | 345 passed, 1 skipped, 17 warnings |
| Python compile checks | Passed |
| `git diff --check` | Passed |
| Live provider request | Not performed; tests use local mocks and no secret |

The branch is `feat/ox-alpha-openrouter-integration`. No commit, push, pull request, or merge was performed as part of this implementation step.
