"""Small cache abstraction: in-memory TTL by default, Redis when REDIS_URL is set.

Public data (courses, college info) is cached for minutes; per-user data for
seconds and is invalidated after every write so answers stay correct.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: float) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class MemoryCache:
    def __init__(self, max_items: int = 2000):
        self._data: dict[str, tuple[float, Any]] = {}
        self._max = max_items

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._data) >= self._max:
            now = time.monotonic()
            for k in [k for k, (e, _) in self._data.items() if e < now] or list(self._data)[: self._max // 10]:
                self._data.pop(k, None)
        self._data[key] = (time.monotonic() + ttl, value)

    def delete_prefix(self, prefix: str) -> None:
        for k in [k for k in self._data if k.startswith(prefix)]:
            self._data.pop(k, None)


class RedisCache:  # pragma: no cover - optional dependency
    def __init__(self, url: str):
        import redis  # type: ignore
        self._r = redis.Redis.from_url(url)

    def get(self, key):
        v = self._r.get(key)
        return json.loads(v) if v else None

    def set(self, key, value, ttl):
        self._r.set(key, json.dumps(value), ex=max(1, int(ttl)))

    def delete_prefix(self, prefix):
        for k in self._r.scan_iter(f"{prefix}*"):
            self._r.delete(k)


def build_cache(redis_url: str = "") -> Cache:
    if redis_url:
        try:
            return RedisCache(redis_url)
        except Exception:
            pass
    return MemoryCache()
