"""Tiny LRU cache for semantic search responses."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


class SearchResponseCache:
    __slots__ = ("_data", "_lock", "max_entries")

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max(0, int(max_entries))
        self._data: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> Any | None:
        if self.max_entries <= 0:
            return None
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def set(self, key: tuple[Any, ...], value: Any) -> None:
        if self.max_entries <= 0:
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
