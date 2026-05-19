"""Persistent usage counters in a separate SQLite file."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


def default_stats_db_path() -> Path:
    env = os.environ.get("ISLAMQA_MCP_STATS_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("data/stats.db").expanduser().resolve()


def stats_visitor_hash(raw_client_key: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{raw_client_key}".encode("utf-8")).hexdigest()


class StatsTracker:
    def __init__(self, db_path: Path | None = None, *, salt: str | None = None) -> None:
        self._path = db_path or default_stats_db_path()
        self._salt = (
            salt or os.environ.get("ISLAMQA_MCP_STATS_SALT", "").strip() or "islamqa-mcp-stats"
        )
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now')),
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                ip_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS unique_visitors (
                ip_hash TEXT PRIMARY KEY,
                first_seen TEXT DEFAULT (datetime('now'))
            );
            """
        )
        self._conn.commit()

    def record(self, source: str, kind: str, visitor_raw: str | None) -> None:
        if source not in ("mcp", "api") or kind not in ("search", "lookup"):
            return
        ip_hash = stats_visitor_hash(visitor_raw, self._salt) if visitor_raw else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (source, kind, ip_hash) VALUES (?, ?, ?)",
                (source, kind, ip_hash),
            )
            if ip_hash:
                self._conn.execute(
                    "INSERT OR IGNORE INTO unique_visitors (ip_hash) VALUES (?)",
                    (ip_hash,),
                )
            self._conn.commit()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT source, kind, COUNT(*) FROM events GROUP BY source, kind"
            )
            rows = cur.fetchall()
            uv = int(self._conn.execute("SELECT COUNT(*) FROM unique_visitors").fetchone()[0])
        mcp_s = mcp_l = api_s = api_l = 0
        for src, k, n in rows:
            if src == "mcp" and k == "search":
                mcp_s = int(n)
            elif src == "mcp" and k == "lookup":
                mcp_l = int(n)
            elif src == "api" and k == "search":
                api_s = int(n)
            elif src == "api" and k == "lookup":
                api_l = int(n)
        return {
            "total_searches": mcp_s + api_s,
            "total_lookups": mcp_l + api_l,
            "unique_visitors": uv,
            "mcp": {"searches": mcp_s, "lookups": mcp_l},
            "api": {"searches": api_s, "lookups": api_l},
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
