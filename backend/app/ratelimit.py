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

    def allow(self, key: str, scale: float = 1.0) -> bool:
        """Take one token from ``key``'s bucket; ``scale`` multiplies capacity/refill (e.g. a shared IP)."""
        now = time.monotonic()
        cap, rate = self.capacity * scale, self.rate * scale
        b = self._buckets.get(key)
        if b is None:
            b = self._buckets[key] = _Bucket(tokens=cap, updated=now)
            if len(self._buckets) > 50000:  # bound memory under a flood of new keys
                self._buckets.clear()
                self._buckets[key] = b
        b.tokens = min(cap, b.tokens + (now - b.updated) * rate)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return True
        return False
