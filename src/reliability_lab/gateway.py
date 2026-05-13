from __future__ import annotations

import time
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None
    route_reason: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        TODO(student): Improve route reasons, cache safety checks, and error handling.
        TODO(student): Add cost budget check — if cumulative cost exceeds a threshold,
        skip expensive providers and route to cache or cheaper fallback.
        """
        start = time.perf_counter()
        if self.cache is not None:
            try:
                cached, score = self.cache.get(prompt)
            except Exception:
                cached, score = None, 0.0
            if cached is not None:
                latency_ms = (time.perf_counter() - start) * 1000
                return GatewayResponse(
                    text=cached,
                    route=f"cache_hit:{score:.2f}",
                    route_reason=f"cache_hit:{score:.2f}",
                    provider=None,
                    cache_hit=True,
                    latency_ms=latency_ms,
                    estimated_cost=0.0,
                )

        last_error: str | None = None
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
                if self.cache is not None:
                    try:
                        self.cache.set(prompt, response.text, {"provider": provider.name})
                    except Exception:
                        pass
                route = "primary" if provider == self.providers[0] else "fallback"
                elapsed_ms = (time.perf_counter() - start) * 1000
                return GatewayResponse(
                    text=response.text,
                    route=route,
                    route_reason=f"{route}:{provider.name}",
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=elapsed_ms,
                    estimated_cost=response.estimated_cost,
                )
            except (ProviderError, CircuitOpenError) as exc:
                last_error = str(exc)
                continue

        elapsed_ms = (time.perf_counter() - start) * 1000
        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            route_reason=f"static_fallback:{last_error or 'unavailable'}",
            provider=None,
            cache_hit=False,
            latency_ms=elapsed_ms,
            estimated_cost=0.0,
            error=last_error,
        )
