# Multi-Provider AI Router Change Record

## Scope

This change adds a configuration-driven provider/model router inside `ai_service`. It extends the existing parsing paths; it does not create a second parsing service and does not move secrets into Core, Telegram, frontend, or source control.

## Supported provider contracts

The router supports OpenRouter and Hugging Face through OpenAI-compatible chat-completion payloads. Hugging Face uses `https://router.huggingface.co/v1/chat/completions` and `HUGGINGFACE_TOKEN`. It supports FAL through its model endpoint contract with `FAL_KEY`, `FAL_MODEL`, and optional `FAL_API_URL`; when the URL is omitted, the official `https://fal.run/{FAL_MODEL}` form is derived.

The provider/model list is configuration-driven through `AI_MODEL_ROUTES` (maximum 20 entries) or the legacy environment variables. Each route has a provider, model, endpoint, secret environment-variable name, protocol, capabilities, and priority. No model is hardcoded as the only supported model. Missing keys or models disable only that route.

## Reliability and safety

Routes are ordered by priority, selected by `text` or `vision` capability, and isolated by a process-local circuit breaker after repeated failures. Existing bounded HTTP retry/backoff remains the transport safety net. The parser returns `provider_rate_limited` or `provider_unavailable` and the HTTP layer returns `503` with `Retry-After`, avoiding a false `200` success. Route telemetry includes provider/model route identity but never API keys or raw Telegram URLs.

Explicit JSON routes accept secret names only through `api_key_env`; raw secrets in route JSON are ignored. Provider URLs are allowlisted for `openrouter.ai`, `router.huggingface.co`, and `fal.run` unless custom routes are explicitly enabled.

## Environment examples

```env
AI_ROUTER_ENABLED=1
AI_PROVIDER_ORDER=openrouter,huggingface,fal
AI_CIRCUIT_FAILURE_THRESHOLD=3
AI_CIRCUIT_COOLDOWN_SECONDS=30
HUGGINGFACE_TOKEN=...
HUGGINGFACE_MODEL=...
FAL_KEY=...
FAL_MODEL=...
```

For multiple models/providers, use `AI_MODEL_ROUTES` with `api_key_env` names. Operators must choose models compatible with the requested capability; model selection is not imposed by this change.

## Validation

- Provider router and parser contracts: 53 passed, 1 skipped.
- Full Python suite: 375 passed, 1 skipped, 17 warnings.
- Python compilation: passed.
- Frontend tests: passed.
- TypeScript check: passed.
- Production frontend build: passed.
- `git diff --check`: passed.

## External references

1. https://fal.ai/docs/documentation/model-apis/overview — fal Model APIs, queue/retry and production calling patterns.
2. https://fal.ai/docs/documentation/setting-up/authentication — FAL API key authentication and `FAL_KEY`.
3. https://huggingface.co/docs/inference-providers/en/tasks/chat-completion — Hugging Face Inference Providers and OpenAI-compatible chat completion.
4. https://openrouter.ai/docs/guides/routing/model-fallbacks — OpenRouter model fallback routing.
5. https://openrouter.ai/docs/guides/routing/provider-selection — OpenRouter provider selection and fallback options.

## Operational limitations

The service cannot validate a provider key or model availability without calling the provider. Production enablement should first set the required Secrets and model routes, then run a smoke test for text and vision. The previously exposed Telegram Bot Token and OpenRouter key must be rotated separately; this code change does not rotate external credentials.
