"""Shared per-host rate limiter (min interval between calls, thread-safe)."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    interval: float     # min seconds between calls
    jitter: float = 0.0  # extra random 0..jitter seconds, so cadence isn't machine-regular


class RateLimiter:
    """Hosts are matched by domain suffix, longest wins; all hosts matching one
    rule share its slot (e.g. www.sec.gov + data.sec.gov share SEC's 10 req/s).
    Unmatched hosts get `default`, each with its own slot."""

    def __init__(self, rules: dict[str, Rule], default: Rule, clock=time.monotonic, sleep=time.sleep):
        self.rules = rules
        self.default = default
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def _match(self, host: str) -> tuple[str, Rule]:
        host = host.lower()
        best = max((s for s in self.rules if host == s or host.endswith("." + s)), key=len, default=None)
        return (best, self.rules[best]) if best else (host, self.default)

    def wait(self, host: str) -> None:
        key, rule = self._match(host)
        # Reserve a slot under the lock, sleep outside it: threads queue up in order
        # and consecutive calls are always >= interval apart.
        with self._lock:
            now = self._clock()
            slot = max(now, self._next.get(key, now))
            self._next[key] = slot + rule.interval + random.uniform(0, rule.jitter)
        if slot > now:
            self._sleep(slot - now)
