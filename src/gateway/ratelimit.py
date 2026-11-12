"""In-memory limits: agent request rate, and password-guessing lockout (spec §10.2)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable


class TokenBucket:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._tokens = self._capacity
        self._last = clock()
        self._clock = clock

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class FailureLimiter:
    """5 failures per IP per 15 min → 429; 20 failures overall → locked for 1 hour."""

    def __init__(
        self,
        per_ip: int = 5,
        window: float = 900,
        global_max: int = 20,
        lockout: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._per_ip = per_ip
        self._window = window
        self._global_max = global_max
        self._lockout = lockout
        self._clock = clock
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._locked_until = 0.0

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self._window:
            q.popleft()

    def blocked(self, ip: str, include_global: bool = True) -> bool:
        now = self._clock()
        if include_global and now < self._locked_until:
            return True
        q = self._by_ip[ip]
        self._trim(q, now)
        return len(q) >= self._per_ip

    def record_failure(self, ip: str, count_global: bool = True) -> None:
        now = self._clock()
        if len(self._by_ip) > 5000:  # spoofed X-Forwarded-For values must not grow memory forever
            for key in [k for k, q in self._by_ip.items() if not q or now - q[-1] > self._window]:
                del self._by_ip[key]
        self._by_ip[ip].append(now)
        if not count_global:
            return
        self._global.append(now)
        self._trim(self._global, now)
        if len(self._global) >= self._global_max:
            self._locked_until = now + self._lockout
            self._global.clear()

    def record_success(self, ip: str) -> None:
        self._by_ip.pop(ip, None)
