"""In-memory TTL cache keyed by URL (thread-safe)."""
from __future__ import annotations

import threading
import time
from typing import Any

# ponytail: in-memory only. Store-backed "fetch a daily file once" lands with store/ (step 3).


class TTLCache:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            expires, value = hit
            if expires <= self._clock():
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
