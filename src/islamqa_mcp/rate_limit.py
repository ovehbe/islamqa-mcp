"""Simple sliding-window rate limits (per key, in-process)."""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    __slots__ = ("_hits", "_lock", "per_minute")

    def __init__(self, per_minute: int | None) -> None:
        self.per_minute = None if per_minute is None or per_minute <= 0 else int(per_minute)
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if self.per_minute is None:
            return True
        now = time.monotonic()
        window = 60.0
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True
