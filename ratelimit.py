"""In-memory, per-key sliding-window rate limiter.

Process-local: counters live in this worker's memory and reset on restart
and aren't shared across workers. That's an accepted tradeoff here — enough
to blunt abusive bursts (e.g. someone hammering /login or /forgot-password),
not a hard security boundary. Same shape as admin/services/ratelimit.py.
"""

from __future__ import annotations

import time
from collections import deque

from bottle import request


class RateLimiter:
    def __init__(self, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}

    def hit(self, key: str) -> bool:
        """Record an attempt for `key`; return True if it is now rate-limited.

        Old timestamps outside the window are discarded on each call, so the
        buckets stay bounded for active keys.
        """
        now = time.time()
        bucket = self._buckets.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.max_hits:
            return True
        bucket.append(now)
        return False


def client_ip() -> str:
    """Best-effort client IP, honoring a proxy's X-Forwarded-For."""
    fwd = request.get_header("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"
