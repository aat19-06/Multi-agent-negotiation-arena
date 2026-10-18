from collections import deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 10000):
        self._buckets: dict[str, tuple[int, deque[float]]] = {}
        self._max_keys = max_keys
        self._lock = Lock()

    def check(
        self,
        request: Request,
        action: str,
        *,
        user_id: int | str | None = None,
        ip_limit: int | None,
        user_limit: int | None = None,
        window_seconds: int = 60,
    ) -> None:
        now = monotonic()
        client_ip = request.client.host if request.client else "unknown"
        limits = []
        if ip_limit is not None:
            limits.append((f"ip:{action}:{client_ip}", ip_limit))
        if user_id is not None and user_limit is not None:
            limits.append((f"user:{action}:{user_id}", user_limit))

        with self._lock:
            active: list[tuple[str, int, deque[float]]] = []
            missing = []
            for key, limit in limits:
                entry = self._buckets.get(key)
                if entry is None:
                    missing.append((key, limit))
                    continue
                bucket_window, bucket = entry
                while bucket and now - bucket[0] >= bucket_window:
                    bucket.popleft()
                if not bucket:
                    del self._buckets[key]
                    missing.append((key, limit))
                    continue
                if len(bucket) >= limit:
                    retry_after = max(1, int(bucket_window - (now - bucket[0])))
                    raise HTTPException(
                        status_code=429,
                        detail="Too many requests. Please try again later.",
                        headers={"Retry-After": str(retry_after)},
                    )
                active.append((key, bucket_window, bucket))

            if len(self._buckets) + len(missing) > self._max_keys:
                for old_key, (bucket_window, bucket) in list(self._buckets.items()):
                    while bucket and now - bucket[0] >= bucket_window:
                        bucket.popleft()
                    if not bucket:
                        del self._buckets[old_key]

            if len(self._buckets) + len(missing) > self._max_keys:
                retry_after = min(
                    (
                        max(1, int(bucket_window - (now - bucket[0])))
                        for bucket_window, bucket in self._buckets.values()
                        if bucket
                    ),
                    default=60,
                )
                raise HTTPException(
                    status_code=429,
                    detail="Rate-limit capacity is busy. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )

            for key, limit in missing:
                bucket = deque()
                self._buckets[key] = (window_seconds, bucket)
                active.append((key, window_seconds, bucket))

            for _, _, bucket in active:
                bucket.append(now)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class ConcurrencyGate:
    def __init__(self, limit: int):
        self._limit = limit
        self._active = 0
        self._lock = Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("Concurrency gate released without an active permit.")
            self._active -= 1

    def reset(self) -> None:
        with self._lock:
            self._active = 0


request_limiter = SlidingWindowLimiter()
ai_request_gate = ConcurrencyGate(limit=2)
simulation_gate = ConcurrencyGate(limit=2)
sse_gate = ConcurrencyGate(limit=20)
