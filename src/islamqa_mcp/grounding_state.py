"""Per-session grounding delivery (nonce + suppress redundant full text)."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class _Entry:
    nonce: str


class GroundingState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, _Entry] = {}

    def fetch(
        self,
        session_key: str,
        *,
        nonce: str | None,
        force_full: bool,
        full_text: str,
    ) -> dict[str, Any]:
        with self._lock:
            prev = self._by_key.get(session_key)
            if prev is not None and not force_full:
                if nonce is not None and nonce != prev.nonce:
                    return {
                        "rules": None,
                        "nonce": prev.nonce,
                        "brief": "Unknown nonce; omit nonce or use force_full=True.",
                        "repeat_suppressed": True,
                        "error": "unknown_nonce",
                    }
                return {
                    "rules": None,
                    "nonce": prev.nonce,
                    "brief": "Grounding rules already sent this session; reuse earlier result.",
                    "repeat_suppressed": True,
                    "error": None,
                }
            new_nonce = secrets.token_hex(12)
            self._by_key[session_key] = _Entry(nonce=new_nonce)
            return {
                "rules": full_text,
                "nonce": new_nonce,
                "brief": None,
                "repeat_suppressed": False,
                "error": None,
            }
