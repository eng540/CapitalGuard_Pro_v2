"""Low-impact, non-financial latency baseline for public platform contracts."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def sample(label: str, url: str, count: int, timeout: float) -> dict[str, object]:
    latencies: list[float] = []
    failures = 0
    for _ in range(count):
        started = time.perf_counter()
        try:
            with urlopen(url, timeout=timeout) as response:
                if response.status != 200:
                    failures += 1
        except (HTTPError, URLError, TimeoutError):
            failures += 1
        latencies.append((time.perf_counter() - started) * 1000)
    return {
        "contract": label,
        "samples": count,
        "failures": failures,
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "max_ms": round(max(latencies), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a low-impact non-financial SLO baseline.")
    parser.add_argument("core_url")
    parser.add_argument("web_url")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--enforce-max-p95-ms", type=float, default=None, help="Optional approved p95 gate; omitted for observation-only baselines.")
    args = parser.parse_args()
    if not 1 <= args.samples <= 20:
        raise SystemExit("samples must be between 1 and 20")
    core = args.core_url.rstrip("/")
    web = args.web_url.rstrip("/")
    results = [
        sample("core_health", f"{core}/health", args.samples, args.timeout_seconds),
        sample("core_v1_status", f"{core}/api/v1/status", args.samples, args.timeout_seconds),
        sample("web_health", f"{web}/health", args.samples, args.timeout_seconds),
    ]
    passed = all(item["failures"] == 0 for item in results)
    if args.enforce_max_p95_ms is not None:
        passed = passed and all(item["p95_ms"] <= args.enforce_max_p95_ms for item in results)
    print(json.dumps({"kind": "R4_NONFINANCIAL_SLO_BASELINE", "samples_per_contract": args.samples, "enforced_max_p95_ms": args.enforce_max_p95_ms, "passed": passed, "contracts": results}, sort_keys=True))
    if not passed:
        raise SystemExit("SLO baseline failed")


if __name__ == "__main__":
    main()
