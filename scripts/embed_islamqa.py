#!/usr/bin/env python3
"""Generate OpenAI embeddings for IslamQA answers → SQLite.

Reads ``data/answers.json``, embeds each answer's combined text with
``text-embedding-3-large`` (3072-d), and stores results in ``data/embeddings.db``.

Incremental: only embeds IDs without a stored vector.  Commits every N rows.
Rate-limit aware with exponential backoff.  Safe to interrupt and resume.

Embedding text strategy (matches assim pattern):
    title_en + question_en + answer_en
With Arabic appended when available (gives the vector bilingual signal).
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import tiktoken
from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM = 3072
MAX_EMBED_TOKENS = 8000

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ANSWERS_FILE = DATA_DIR / "answers.json"
EMBEDDINGS_DB = DATA_DIR / "embeddings.db"

_enc: tiktoken.Encoding | None = None


def _tiktoken_enc() -> tiktoken.Encoding:
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def truncate_to_tokens(text: str, max_tokens: int = MAX_EMBED_TOKENS) -> str:
    t = text.strip()
    if not t:
        return ""
    enc = _tiktoken_enc()
    ids = enc.encode(t)
    if len(ids) <= max_tokens:
        return t
    return enc.decode(ids[:max_tokens])


def to_blob(vec: list[float]) -> bytes:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return pickle.dumps(arr, protocol=4)


def build_embed_text(entry: dict) -> str:
    """Combine fields into embedding input text.

    Priority: English title + question + answer, then Arabic if available.
    This gives the vector bilingual signal while keeping English primary.
    """
    parts = []

    for lang_suffix in ("_en", "_ar"):
        title = entry.get(f"title{lang_suffix}", "")
        question = entry.get(f"question{lang_suffix}", "")
        answer = entry.get(f"answer{lang_suffix}", "")
        section = " ".join(p for p in [title, question, answer] if p)
        if section.strip():
            parts.append(section)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY,
    title_en    TEXT,
    title_ar    TEXT,
    question_en TEXT,
    question_ar TEXT,
    answer_en   TEXT,
    answer_ar   TEXT,
    embedding   BLOB
);
"""

INSERT_SQL = """
INSERT OR REPLACE INTO embeddings (
    id, title_en, title_ar, question_en, question_ar, answer_en, answer_ar, embedding
) VALUES (?,?,?,?,?,?,?,?)
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_existing_ids(conn: sqlite3.Connection) -> set[int]:
    """IDs that already have a vector (failed NULL rows are retried on resume)."""
    cur = conn.execute("SELECT id FROM embeddings WHERE embedding IS NOT NULL")
    return {row[0] for row in cur.fetchall()}


def _row_params(entry: dict, blob: bytes | None) -> tuple:
    return (
        entry["id"],
        entry.get("title_en"),
        entry.get("title_ar"),
        entry.get("question_en"),
        entry.get("question_ar"),
        entry.get("answer_en"),
        entry.get("answer_ar"),
        blob,
    )


# ---------------------------------------------------------------------------
# OpenAI calls with retries
# ---------------------------------------------------------------------------

def _is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _is_context_length_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "maximum context length" in msg or "8192" in msg


def embed_texts(client: OpenAI, texts: Sequence[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=list(texts))
    data = sorted(resp.data, key=lambda x: x.index)
    return [d.embedding for d in data]


def embed_one_safe(client: OpenAI, text: str) -> list[float]:
    """Embed with progressive truncation fallback on context length errors."""
    last_exc: BaseException | None = None
    for limit in (MAX_EMBED_TOKENS, 6000, 4000, 2000, 1000):
        payload = truncate_to_tokens(text, max_tokens=limit)
        try:
            return embed_texts(client, [payload])[0]
        except BaseException as exc:
            if _is_context_length_error(exc):
                last_exc = exc
                continue
            raise
    assert last_exc is not None
    raise last_exc


def embed_batch_safe(client: OpenAI, texts: list[str]) -> list[list[float]]:
    trimmed = [truncate_to_tokens(t) for t in texts]
    try:
        return embed_texts(client, trimmed)
    except BaseException as exc:
        if not _is_context_length_error(exc):
            raise
        return [embed_one_safe(client, t) for t in texts]


def call_with_retries(
    client: OpenAI,
    texts: list[str],
    *,
    max_retries: int = 6,
    backoff_base: float = 30.0,
    backoff_max: float = 300.0,
) -> list[list[float]]:
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            if len(texts) == 1:
                return [embed_one_safe(client, texts[0])]
            return embed_batch_safe(client, texts)
        except BaseException as exc:
            last_exc = exc
            if attempt >= max_retries:
                raise
            if _is_rate_limited(exc):
                wait = min(backoff_base * (2**attempt) + random.uniform(0, 3), backoff_max)
                print(f"  Rate limited (attempt {attempt + 1}/{max_retries}), waiting {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"  API error (attempt {attempt + 1}): {exc}")
                time.sleep(0.5)
    raise RuntimeError("unreachable") from last_exc


# ---------------------------------------------------------------------------
# Main embedding loop
# ---------------------------------------------------------------------------

def run_embed(
    *,
    db_path: Path = EMBEDDINGS_DB,
    commit_every: int = 10,
    batch_size: int = 1,
    sleep_between: float = 0.12,
    max_items: int | None = None,
) -> None:
    print("Loading answers.json...")
    with open(ANSWERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"Total answers in corpus: {len(data)}")

    conn = init_db(db_path)
    existing = load_existing_ids(conn)
    print(f"Existing embeddings in DB: {len(existing)}")

    missing = [entry for entry in data if entry["id"] not in existing]
    if max_items:
        missing = missing[:max_items]
    print(f"Answers to embed: {len(missing)}")
    bs = max(1, batch_size)
    if bs > 1:
        print(f"Batch size: {bs} (sleep {sleep_between}s between API batches)")

    if not missing:
        print("Nothing to do.")
        conn.close()
        return

    client = OpenAI()
    ok = 0
    bad = 0
    since_commit = 0
    total = len(missing)
    idx = 0

    def commit_if_needed(*, force: bool = False) -> None:
        nonlocal since_commit
        if force or since_commit >= commit_every:
            conn.commit()
            since_commit = 0

    def write_success(entry: dict, vec: list[float]) -> None:
        nonlocal ok, since_commit
        conn.execute(INSERT_SQL, _row_params(entry, to_blob(vec)))
        ok += 1
        since_commit += 1
        commit_if_needed()

    def write_failed(entry: dict) -> None:
        nonlocal bad, since_commit
        conn.execute(INSERT_SQL, _row_params(entry, None))
        bad += 1
        since_commit += 1
        commit_if_needed()

    def embed_entries_one_by_one(entries: list[dict]) -> None:
        nonlocal ok, bad
        for entry in entries:
            aid = entry["id"]
            text = build_embed_text(entry)
            if not text.strip():
                print(f"  Skipping {aid}: no text")
                bad += 1
                continue
            try:
                vec = call_with_retries(client, [text])[0]
                write_success(entry, vec)
            except Exception as exc:
                print(f"  Failed {aid}: {exc}")
                write_failed(entry)
                time.sleep(0.5)

    while idx < total:
        batch_entries: list[dict] = []
        batch_texts: list[str] = []

        while idx < total and len(batch_entries) < bs:
            entry = missing[idx]
            idx += 1
            text = build_embed_text(entry)
            if not text.strip():
                print(f"  Skipping {entry['id']}: no text")
                bad += 1
                continue
            batch_entries.append(entry)
            batch_texts.append(text)

        if not batch_entries:
            continue

        try:
            vectors = call_with_retries(client, batch_texts)
        except Exception as exc:
            print(f"  batch failed after retries (start id={batch_entries[0]['id']}): {exc}")
            embed_entries_one_by_one(batch_entries)
            commit_if_needed(force=True)
            time.sleep(sleep_between)
            continue

        if len(vectors) != len(batch_entries):
            print(f"  API length mismatch; per-item fallback from id={batch_entries[0]['id']}")
            embed_entries_one_by_one(batch_entries)
            commit_if_needed(force=True)
            time.sleep(sleep_between)
            continue

        for entry, vec in zip(batch_entries, vectors, strict=True):
            write_success(entry, vec)

        commit_if_needed(force=True)

        if ok and ok % max(commit_every * 20, 200) == 0:
            print(f"  Progress: {ok}/{total} embedded, {bad} failed, ~{max(0, total - idx)} remaining")

        time.sleep(sleep_between)

    commit_if_needed(force=True)
    conn.close()
    print(f"\nDone. Embedded: {ok}, Failed/skipped: {bad}, Total with vectors: {len(existing) + ok}")


def show_stats(db_path: Path = EMBEDDINGS_DB) -> None:
    if not db_path.exists():
        print("No embeddings DB yet.")
        return
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    with_emb = conn.execute("SELECT COUNT(*) FROM embeddings WHERE embedding IS NOT NULL").fetchone()[0]
    null_emb = total - with_emb
    conn.close()
    print(f"Embeddings DB: {total} rows, {with_emb} with vectors, {null_emb} NULL (failed)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed IslamQA answers with OpenAI")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Generate embeddings (incremental)")
    run.add_argument("--commit-every", type=int, default=10, help="Commit to DB every N embeddings")
    run.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Texts per OpenAI API call (1 = slowest/safest; try 32–64 for bulk)",
    )
    run.add_argument(
        "--sleep",
        type=float,
        default=0.12,
        help="Sleep after each API call or batch (seconds)",
    )
    run.add_argument("--max", type=int, default=None, help="Max items to embed this run")
    run.add_argument("--db", type=str, default=None, help="Override DB path")

    stats_cmd = sub.add_parser("stats", help="Show embedding DB statistics")
    stats_cmd.add_argument("--db", type=str, default=None, help="Override DB path")

    args = parser.parse_args()

    if args.command == "run":
        db = Path(args.db) if args.db else EMBEDDINGS_DB
        run_embed(
            db_path=db,
            commit_every=args.commit_every,
            batch_size=args.batch_size,
            sleep_between=args.sleep,
            max_items=args.max,
        )
    elif args.command == "stats":
        db = Path(args.db) if args.db else EMBEDDINGS_DB
        show_stats(db)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
