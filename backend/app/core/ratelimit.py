"""Tiny in-process rate limiter.

Protects login / upload / AI endpoints from abuse and accidental request
storms. It is intentionally simple: process local, fixed window, no Redis.
When the deployment grows past a single process, replace this class with a
Redis backed implementation - the API layer only depends on the check method.
"""

import time
from collections import defaultdict, deque

from app.core.errors import ServiceError


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def check(self, key: str) -> None:
        now = time.monotonic()
        window_start = now - self.window_seconds
        bucket = self._hits[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= self.max_calls:
            retry_after = int(self.window_seconds - (now - bucket[0])) + 1
            raise ServiceError(
                "操作过于频繁，请在 " + str(max(1, retry_after)) + " 秒后重试",
                status_code=429,
                code="rate_limited",
            )
        bucket.append(now)
