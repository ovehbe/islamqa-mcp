#!/usr/bin/env python3
"""Build data/islamqa.db from answers.json + embeddings.db."""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from islamqa_mcp.pipeline.schema import apply_schema

DATA_DIR = ROOT / "data"
ANSWERS_FILE = DATA_DIR / "answers.json"
EMBEDDINGS_DB = DATA_DIR / "embeddings.db"
DEFAULT_DB = DATA_DIR / "islamqa.db"


def load_embeddings(path: Path) -> dict[int, bytes | None]:
    if not path.is_file():
        return {}
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute("SELECT id, embedding FROM embeddings")
        return {int(row[0]): row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def upsert_category(
    cur: sqlite3.Cursor,
    cat_id: int,
    name_en: str | None,
    name_ar: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO categories (id, name_en, name_ar) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name_en = COALESCE(excluded.name_en, categories.name_en),
            name_ar = COALESCE(excluded.name_ar, categories.name_ar)
        """,
        (cat_id, name_en, name_ar),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Build islamqa.db from scraped data")
    p.add_argument("--answers", type=Path, default=ANSWERS_FILE)
    p.add_argument("--embeddings-db", type=Path, default=EMBEDDINGS_DB)
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    p.add_argument("--fresh", action="store_true", help="Delete existing DB before build")
    args = p.parse_args()

    if not args.answers.is_file():
        print(f"Missing {args.answers}", file=sys.stderr)
        return 1

    with open(args.answers, "r", encoding="utf-8") as f:
        answers = json.load(f)

    emb_map = load_embeddings(args.embeddings_db)
    print(f"Loaded {len(answers)} answers, {len(emb_map)} embedding rows")

    if args.fresh and args.db_path.is_file():
        args.db_path.unlink()

    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db_path))
    apply_schema(conn)
    cur = conn.cursor()

    cur.execute("DELETE FROM answer_categories")
    cur.execute("DELETE FROM answers")
    cur.execute("DELETE FROM categories")

    with_emb = 0
    for entry in answers:
        aid = int(entry["id"])
        blob = emb_map.get(aid)
        if blob:
            with_emb += 1

        cur.execute(
            """
            INSERT INTO answers (
                id, title_en, title_ar, question_en, question_ar,
                answer_en, answer_ar, date_created, date_modified,
                url_en, url_ar, embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aid,
                entry.get("title_en"),
                entry.get("title_ar"),
                entry.get("question_en"),
                entry.get("question_ar"),
                entry.get("answer_en"),
                entry.get("answer_ar"),
                entry.get("date_created"),
                entry.get("date_modified"),
                entry.get("url_en"),
                entry.get("url_ar"),
                blob,
            ),
        )

        en_by_id: dict[int, str | None] = {}
        ar_by_id: dict[int, str | None] = {}
        for cat in entry.get("categories_en", []) or []:
            try:
                en_by_id[int(cat["id"])] = cat.get("name")
            except (KeyError, TypeError, ValueError):
                pass
        for cat in entry.get("categories_ar", []) or []:
            try:
                ar_by_id[int(cat["id"])] = cat.get("name")
            except (KeyError, TypeError, ValueError):
                pass
        all_cat_ids = set(en_by_id) | set(ar_by_id)
        for cid in all_cat_ids:
            upsert_category(cur, cid, en_by_id.get(cid), ar_by_id.get(cid))
            cur.execute(
                "INSERT OR IGNORE INTO answer_categories (answer_id, category_id) VALUES (?, ?)",
                (aid, cid),
            )

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    cats = cur.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()

    print(f"Built {args.db_path}: {total} answers, {cats} categories, {with_emb} with embeddings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
