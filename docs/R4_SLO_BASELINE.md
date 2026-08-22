# R4 Non-financial SLO Baseline

`scripts/platform_slo_baseline.py` measures only three public, non-financial contracts: Core health, Core API v1 status, and Web health. Its default is **10 sequential samples per contract** in observation mode. It makes no authenticated calls, reads no financial data, and sends no trade commands.

```bash
python3 scripts/platform_slo_baseline.py https://core.example https://web.example
```

The result is one masked JSON object containing contract labels, sample count, failure count, p50, p95, and maximum latency. URLs, secrets, user identifiers, portfolio data, and recommendation contents are deliberately omitted. An approved guardrail can be enforced explicitly with `--enforce-max-p95-ms <value>`; it is not inferred automatically from the first baseline.

> This is a lightweight release baseline, not a load test or capacity certification. It does not close error-budget, canary, distributed rate-limit, or production load-testing gates. A larger traffic envelope and any strict SLO gate need an approved staging plan before execution.
