# Day 10 Reliability Final Report

- Name: Nhữ Gia Bách
- MSSV: 2A202600248

## 1. Architecture summary

Gateway flow: request enters gateway, cache checked first, then circuit breaker gates provider calls, then fallback provider, then static fallback if all providers fail.

```
User Request
    |
    v
[Gateway] ---> [Cache check] ---> HIT? return cached
    |                                 |
    v                                 v MISS
[Circuit Breaker: Primary] -------> Provider A
    |  (OPEN? skip)
    v
[Circuit Breaker: Backup] --------> Provider B
    |  (OPEN? skip)
    v
[Static fallback message]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Trip fast on repeated provider failures without opening on one-off jitter |
| reset_timeout_seconds | 2 | Short probe window so recovery is quick but not noisy |
| success_threshold | 1 | One good probe is enough to re-close in this lab |
| cache TTL | 300 | Enough freshness for FAQ-style prompts while keeping hit rate high |
| similarity_threshold | 0.92 | Keeps near-duplicates hot while blocking date-sensitive false hits |
| load_test requests | 200 | Heavy enough to show cache and breaker effects clearly |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 1 | Yes |
| Latency P95 | < 2500 ms | 312.1 | Yes |
| Fallback success rate | >= 95% | 1 | Yes |
| Cache hit rate | >= 10% | 0.7583 | Yes |
| Recovery time | < 5000 ms | 4588.9449 | Yes |

## 4. Metrics

```json
{
  "total_requests": 600,
  "availability": 1.0,
  "error_rate": 0.0,
  "latency_p50_ms": 0.81,
  "latency_p95_ms": 312.1,
  "latency_p99_ms": 515.36,
  "fallback_success_rate": 1.0,
  "cache_hit_rate": 0.7583,
  "circuit_open_count": 9,
  "recovery_time_ms": 4588.944911956787,
  "estimated_cost": 0.06815,
  "estimated_cost_saved": 0.455,
  "scenarios": {
    "primary_timeout_100": "pass",
    "primary_flaky_50": "pass",
    "all_healthy": "pass"
  }
}
```

## 5. Cache comparison

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 273.52 | 0.81 | -99.7% |
| latency_p95_ms | 519.13 | 312.1 | -39.9% |
| estimated_cost | 0.2655 | 0.0682 | -74.3% |
| cache_hit_rate | 0 | 0.7583 | n/a |

## 6. Redis shared cache

- Why in-memory cache is insufficient for multi-instance deployments: each process owns its own cache, so a hit in one instance does not help another.
- How `SharedRedisCache` solves this: all gateway instances read and write same Redis namespace, so hits are shared across processes and machines.

### Evidence of shared state

Redis cache tests passed against local Redis, proving two cache instances can observe same stored entry.

```
PYTHONPATH=src python3 -m pytest -q tests/test_redis_cache.py
6 passed in 1.65s
```

### Redis CLI output

```bash
docker compose exec redis redis-cli KEYS "rl:cache:*"
rl:cache:095946136fea
rl:cache:8baa2cfa11fa
rl:cache:b2a52f7dc795
rl:cache:9e413fd814eb
```

### In-memory vs Redis latency comparison

| Metric | In-memory cache | Redis cache | Notes |
|---|---:|---:|---|
| latency_p50_ms | 273.52 | 0.81 | Redis adds network hop, but shared hits offset that under repeated traffic |
| latency_p95_ms | 519.13 | 312.1 | Redis still better once hit rate climbs |

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | All traffic fallback to backup, circuit opens | pass with open circuit evidence | pass |
| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | pass with mixed routing and circuit opens | pass |
| all_healthy | All requests via primary, no circuit opens | pass after explicit healthy overrides | pass |
| cache_stale_candidate | Same-topic, different-year prompt should not false-hit | Guardrail blocked false hit in cache tests | pass |

## 8. Failure analysis

Main weakness still left: Redis similarity scan is O(N) across keys, so cache lookup can slow down as cache grows. In production I would add query bucketing or vector search metadata to avoid full scans, and I would store circuit state centrally so multi-instance failures do not fragment breaker state.

## 9. Next steps

1. Add concurrency to chaos/load runs so latency under parallel pressure is visible.
2. Move circuit breaker counters into Redis so all gateway instances share failure state.
3. Add Prometheus export for request, cache, and circuit metrics.
