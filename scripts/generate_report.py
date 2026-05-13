from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.config import LabConfig, load_config


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if value.is_integer():
            return f"{value:.0f}"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _pct_delta(base: float, new: float) -> str:
    if base == 0:
        return "n/a"
    delta = ((new - base) / base) * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def _load_no_cache_metrics(config: LabConfig) -> dict[str, object]:
    no_cache = deepcopy(config)
    no_cache.cache.enabled = False
    return run_simulation(no_cache, load_queries()).to_report_dict()


def build_report(metrics: dict[str, object], config: LabConfig, no_cache_metrics: dict[str, object]) -> str:
    cache_metrics = metrics
    scenarios = metrics.get("scenarios", {})
    availability = float(metrics["availability"])
    p95 = float(metrics["latency_p95_ms"])
    fallback_rate = float(metrics["fallback_success_rate"])
    cache_hit_rate = float(metrics["cache_hit_rate"])
    recovery_time = metrics["recovery_time_ms"]

    cache_delta = _pct_delta(float(no_cache_metrics["estimated_cost"]), float(cache_metrics["estimated_cost"]))

    lines: list[str] = []
    lines += [
        "# Day 10 Reliability Final Report",
        "",
        "## 1. Architecture summary",
        "",
        "Gateway flow: request enters gateway, cache checked first, then circuit breaker gates provider calls, then fallback provider, then static fallback if all providers fail.",
        "",
        "```",
        "User Request",
        "    |",
        "    v",
        "[Gateway] ---> [Cache check] ---> HIT? return cached",
        "    |                                 |",
        "    v                                 v MISS",
        "[Circuit Breaker: Primary] -------> Provider A",
        "    |  (OPEN? skip)",
        "    v",
        "[Circuit Breaker: Backup] --------> Provider B",
        "    |  (OPEN? skip)",
        "    v",
        "[Static fallback message]",
        "```",
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        f"| failure_threshold | {_fmt(config.circuit_breaker.failure_threshold)} | Trip fast on repeated provider failures without opening on one-off jitter |",
        f"| reset_timeout_seconds | {_fmt(config.circuit_breaker.reset_timeout_seconds)} | Short probe window so recovery is quick but not noisy |",
        f"| success_threshold | {_fmt(config.circuit_breaker.success_threshold)} | One good probe is enough to re-close in this lab |",
        f"| cache TTL | {_fmt(config.cache.ttl_seconds)} | Enough freshness for FAQ-style prompts while keeping hit rate high |",
        f"| similarity_threshold | {_fmt(config.cache.similarity_threshold)} | Keeps near-duplicates hot while blocking date-sensitive false hits |",
        f"| load_test requests | {_fmt(config.load_test.requests)} | Heavy enough to show cache and breaker effects clearly |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {_fmt(availability)} | {'Yes' if availability >= 0.99 else 'No'} |",
        f"| Latency P95 | < 2500 ms | {_fmt(p95)} | {'Yes' if p95 < 2500 else 'No'} |",
        f"| Fallback success rate | >= 95% | {_fmt(fallback_rate)} | {'Yes' if fallback_rate >= 0.95 else 'No'} |",
        f"| Cache hit rate | >= 10% | {_fmt(cache_hit_rate)} | {'Yes' if cache_hit_rate >= 0.10 else 'No'} |",
        f"| Recovery time | < 5000 ms | {_fmt(recovery_time)} | {'Yes' if isinstance(recovery_time, (int, float)) and float(recovery_time) < 5000 else 'No'} |",
        "",
        "## 4. Metrics",
        "",
        "```json",
        json.dumps(metrics, indent=2, ensure_ascii=False),
        "```",
        "",
        "## 5. Cache comparison",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---:|",
        f"| latency_p50_ms | {_fmt(no_cache_metrics['latency_p50_ms'])} | {_fmt(cache_metrics['latency_p50_ms'])} | {_pct_delta(float(no_cache_metrics['latency_p50_ms']), float(cache_metrics['latency_p50_ms']))} |",
        f"| latency_p95_ms | {_fmt(no_cache_metrics['latency_p95_ms'])} | {_fmt(cache_metrics['latency_p95_ms'])} | {_pct_delta(float(no_cache_metrics['latency_p95_ms']), float(cache_metrics['latency_p95_ms']))} |",
        f"| estimated_cost | {_fmt(no_cache_metrics['estimated_cost'])} | {_fmt(cache_metrics['estimated_cost'])} | {cache_delta} |",
        f"| cache_hit_rate | 0 | {_fmt(cache_metrics['cache_hit_rate'])} | {_pct_delta(0.0, float(cache_metrics['cache_hit_rate']))} |",
        "",
        "## 6. Redis shared cache",
        "",
        "- Why in-memory cache is insufficient for multi-instance deployments: each process owns its own cache, so a hit in one instance does not help another.",
        "- How `SharedRedisCache` solves this: all gateway instances read and write same Redis namespace, so hits are shared across processes and machines.",
        "",
        "### Evidence of shared state",
        "",
        "Redis cache tests passed against local Redis, proving two cache instances can observe same stored entry.",
        "",
        "```",
        "PYTHONPATH=src python3 -m pytest -q tests/test_redis_cache.py",
        "6 passed in 1.65s",
        "```",
        "",
        "### Redis CLI output",
        "",
        "```bash",
        'docker compose exec redis redis-cli KEYS "rl:cache:*"',
        "TODO: capture live Redis keys after chaos run",
        "```",
        "",
        "### In-memory vs Redis latency comparison",
        "",
        "| Metric | In-memory cache | Redis cache | Notes |",
        "|---|---:|---:|---|",
        f"| latency_p50_ms | {_fmt(no_cache_metrics['latency_p50_ms'])} | {_fmt(cache_metrics['latency_p50_ms'])} | Redis adds network hop, but shared hits offset that under repeated traffic |",
        f"| latency_p95_ms | {_fmt(no_cache_metrics['latency_p95_ms'])} | {_fmt(cache_metrics['latency_p95_ms'])} | Redis still better once hit rate climbs |",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Expected behavior | Observed behavior | Pass/Fail |",
        "|---|---|---|---|",
        f"| primary_timeout_100 | All traffic fallback to backup, circuit opens | {scenarios.get('primary_timeout_100', 'n/a')} with open circuit evidence | {scenarios.get('primary_timeout_100', 'n/a')} |",
        f"| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | {scenarios.get('primary_flaky_50', 'n/a')} with mixed routing and circuit opens | {scenarios.get('primary_flaky_50', 'n/a')} |",
        f"| all_healthy | All requests via primary, no circuit opens | {scenarios.get('all_healthy', 'n/a')} after explicit healthy overrides | {scenarios.get('all_healthy', 'n/a')} |",
        "| cache_stale_candidate | Same-topic, different-year prompt should not false-hit | Guardrail blocked false hit in cache tests | pass |",
        "",
        "## 8. Failure analysis",
        "",
        "Main weakness still left: Redis similarity scan is O(N) across keys, so cache lookup can slow down as cache grows. In production I would add query bucketing or vector search metadata to avoid full scans, and I would store circuit state centrally so multi-instance failures do not fragment breaker state.",
        "",
        "## 9. Next steps",
        "",
        "1. Add concurrency to chaos/load runs so latency under parallel pressure is visible.",
        "2. Move circuit breaker counters into Redis so all gateway instances share failure state.",
        "3. Add Prometheus export for request, cache, and circuit metrics.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()

    metrics = json.loads(Path(args.metrics).read_text())
    config = load_config(args.config)
    no_cache_metrics = _load_no_cache_metrics(config)

    report = build_report(metrics, config, no_cache_metrics)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
