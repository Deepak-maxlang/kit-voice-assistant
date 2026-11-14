"""In-memory token-bucket rate limiter keyed by session or client IP."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        b = self._buckets.get(key)
        if b is None:
            b = self._buckets[key] = _Bucket(tokens=self.capacity, updated=now)
        b.tokens = min(self.capacity, b.tokens + (now - b.updated) * self.rate)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return True
        return False
