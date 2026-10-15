"""Small circuit breaker for CRM 5xx and rate limits."""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, threshold: int = 3, open_seconds: int = 60):
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = time.monotonic() + self.open_seconds

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0
