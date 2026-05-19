"""Runtime configuration (YAML + environment)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _coerce_rpm(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return None if n <= 0 else n


def _yaml_options(doc: dict[str, Any]) -> dict[str, Any]:
    emb = doc.get("embedding") or {}
    rl = doc.get("rate_limit") or {}
    qm = emb.get("query_model", "text-embedding-3-large")
    return {
        "query_embedding_model": str(qm).strip() or "text-embedding-3-large",
        "rate_limit_search_per_minute": _coerce_rpm(rl.get("search_per_minute", 60)),
        "search_cache_max_entries": int(rl.get("search_cache_max_entries", 256) or 0),
    }


def _resolve_db_path(doc: dict[str, Any], config_yaml: Path | None) -> Path:
    env = os.environ.get("ISLAMQA_MCP_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    raw = (doc.get("database") or {}).get("path")
    if isinstance(raw, str) and raw.strip():
        p = Path(raw.strip())
        if not p.is_absolute():
            base = config_yaml.parent if config_yaml is not None else Path.cwd()
            return (base / p).resolve()
        return p.expanduser().resolve()
    return Path("data/islamqa.db").expanduser().resolve()


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    query_embedding_model: str
    rate_limit_search_per_minute: int | None
    search_cache_max_entries: int


def load_app_config(*, config_yaml: Path | None = None) -> AppConfig:
    doc: dict[str, Any] = {}
    if config_yaml is not None and config_yaml.is_file():
        doc = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}

    y = _yaml_options(doc)
    db_path = _resolve_db_path(doc, config_yaml)

    qm = os.environ.get("ISLAMQA_MCP_QUERY_EMBEDDING_MODEL", "").strip()
    query_embedding_model = qm or y["query_embedding_model"]

    rpm_env = os.environ.get("ISLAMQA_MCP_RATE_LIMIT_SEARCH_RPM", "").strip().lower()
    if rpm_env in ("", "0", "off", "false", "none", "disable"):
        rate_limit_search_per_minute = None
    elif rpm_env:
        rate_limit_search_per_minute = _coerce_rpm(rpm_env)
    else:
        rate_limit_search_per_minute = y["rate_limit_search_per_minute"]

    cache_env = os.environ.get("ISLAMQA_MCP_SEARCH_CACHE_MAX", "").strip()
    if cache_env:
        search_cache_max_entries = max(0, int(cache_env))
    else:
        search_cache_max_entries = max(0, int(y["search_cache_max_entries"]))

    return AppConfig(
        db_path=db_path,
        query_embedding_model=query_embedding_model,
        rate_limit_search_per_minute=rate_limit_search_per_minute,
        search_cache_max_entries=search_cache_max_entries,
    )
