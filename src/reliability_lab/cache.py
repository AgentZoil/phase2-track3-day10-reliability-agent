from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    cleaned = re.sub(r"\s+", " ", text.lower().strip())
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _false_hit_cutoff(threshold: float) -> float:
    return max(0.4, threshold * 0.75)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """Simple in-memory cache skeleton.

    TODO(student): Add a better semantic similarity function and false-hit guardrails.
    Use the module-level _is_uncacheable() and _looks_like_false_hit() helpers in your
    get() and set() methods.  For production, replace with SharedRedisCache.
    """

    def __init__(self, ttl_seconds: int, similarity_threshold: float):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []

    def get(self, query: str) -> tuple[str | None, float]:
        if _is_uncacheable(query):
            return None, 0.0

        best_value: str | None = None
        best_score = 0.0
        best_key: str | None = None
        now = time.time()
        self._entries = [e for e in self._entries if now - e.created_at <= self.ttl_seconds]
        for entry in self._entries:
            if _normalized_text(query) == _normalized_text(entry.key):
                return entry.value, 1.0
                score = self.similarity(query, entry.key)
                if score > best_score:
                    best_score = score
                    best_value = entry.value
                    best_key = entry.key
        if best_value is not None and best_key is not None and _looks_like_false_hit(query, best_key):
            if best_score >= _false_hit_cutoff(self.similarity_threshold):
                return None, best_score
        if best_score >= self.similarity_threshold and best_value is not None:
            return best_value, best_score
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        if _is_uncacheable(query):
            return
        self._entries.append(CacheEntry(query, value, time.time(), metadata or {}))

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Deterministic similarity using token + character overlap.

        TODO(student): Improve with embeddings or a deterministic vectorizer.
        """
        if _normalized_text(a) == _normalized_text(b):
            return 1.0

        token_score = _jaccard(_tokenize(a), _tokenize(b))
        left_ngrams = sorted(_char_ngrams(a, 3))
        right_ngrams = sorted(_char_ngrams(b, 3))
        if left_ngrams and right_ngrams:
            vocab = sorted(set(left_ngrams) | set(right_ngrams))
            left_vec = np.array([left_ngrams.count(term) for term in vocab], dtype=float)
            right_vec = np.array([right_ngrams.count(term) for term in vocab], dtype=float)
            denom = float(np.linalg.norm(left_vec) * np.linalg.norm(right_vec))
            char_score = float(left_vec.dot(right_vec) / denom) if denom else 0.0
        else:
            char_score = 0.0

        left_years = re.findall(r"\b\d{4}\b", a)
        right_years = re.findall(r"\b\d{4}\b", b)
        year_penalty = 0.0 if not left_years or not right_years or set(left_years) == set(right_years) else 0.25

        return max(0.0, min(1.0, 0.65 * token_score + 0.35 * char_score - year_penalty))


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments.

    TODO(student): Implement the get() and set() methods using Redis commands
    so that cache state is shared across multiple gateway instances.

    Data model (suggested):
        Key    = "{prefix}{query_hash}"   (Redis String namespace)
        Value  = Redis Hash with fields:  "query", "response"
        TTL    = Redis EXPIRE (automatic cleanup — no manual eviction)

    For similarity lookup: SCAN all keys with self.prefix, HGET each entry's
    "query" field, compute similarity locally via ResponseCache.similarity().

    Provided helpers:
        _is_uncacheable(query)          — True if privacy-sensitive
        _looks_like_false_hit(q, key)   — True if 4-digit numbers differ
        self._query_hash(query)         — deterministic short hash for Redis key
        ResponseCache.similarity(a, b)  — reuse your improved similarity function
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis.

        TODO(student): Implement cache lookup.  Suggested steps:
        1. Return (None, 0.0) if _is_uncacheable(query)
        2. Build exact-match key: f"{self.prefix}{self._query_hash(query)}"
        3. Try self._redis.hget(key, "response") — if found return (response, 1.0)
        4. Otherwise self._redis.scan_iter(f"{self.prefix}*") to iterate all cached keys
        5. For each key, HGET "query" field and compute
           ResponseCache.similarity(query, cached_query)
        6. Track best match that is >= self.similarity_threshold
        7. Before returning a match, check _looks_like_false_hit(); if true,
           append to self.false_hit_log and return (None, best_score)
        """
        if _is_uncacheable(query):
            return None, 0.0

        try:
            exact_key = f"{self.prefix}{self._query_hash(query)}"
            exact = self._redis.hget(exact_key, "response")
            if exact is not None:
                return exact, 1.0

            best_value: str | None = None
            best_score = 0.0
            best_query: str | None = None
            for key in self._redis.scan_iter(f"{self.prefix}*"):
                cached_query = self._redis.hget(key, "query")
                cached_response = self._redis.hget(key, "response")
                if not cached_query or cached_response is None:
                    continue
                if _normalized_text(query) == _normalized_text(cached_query):
                    return cached_response, 1.0
                score = ResponseCache.similarity(query, cached_query)
                if score > best_score:
                    best_score = score
                    best_value = cached_response
                    best_query = cached_query
            if best_value is not None and best_query is not None and _looks_like_false_hit(query, best_query):
                if best_score >= _false_hit_cutoff(self.similarity_threshold):
                    self.false_hit_log.append(
                        {"query": query, "cached_query": best_query, "score": best_score, "reason": "year_mismatch"}
                    )
                    return None, best_score
            if best_value is not None and best_score >= self.similarity_threshold:
                return best_value, best_score
            return None, best_score
        except Exception:
            return None, 0.0

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL.

        TODO(student): Implement cache storage.  Suggested steps:
        1. Return immediately if _is_uncacheable(query)
        2. Build key: f"{self.prefix}{self._query_hash(query)}"
        3. self._redis.hset(key, mapping={"query": query, "response": value})
        4. self._redis.expire(key, self.ttl_seconds)
        """
        if _is_uncacheable(query):
            return

        try:
            key = f"{self.prefix}{self._query_hash(query)}"
            mapping = {"query": query, "response": value}
            if metadata:
                mapping.update({f"meta:{k}": v for k, v in metadata.items()})
            self._redis.hset(key, mapping=mapping)
            self._redis.expire(key, self.ttl_seconds)
        except Exception:
            return

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
