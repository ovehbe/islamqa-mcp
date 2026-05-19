"""Read-only SQLite access for MCP tools and REST API."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any


def _like_term(term: str) -> str:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


class IslamQAStore:
    def __init__(self, db_path: Path) -> None:
        if not db_path.is_file():
            raise FileNotFoundError(f"islamqa database not found: {db_path}")
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only = ON")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def list_categories(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT c.id, c.name_en, c.name_ar,
                   COUNT(ac.answer_id) AS answer_count
            FROM categories c
            LEFT JOIN answer_categories ac ON ac.category_id = c.id
            GROUP BY c.id
            ORDER BY answer_count DESC, c.name_en
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def get_category_id(self, category_ref: str | int) -> int | None:
        if isinstance(category_ref, int) or (isinstance(category_ref, str) and category_ref.isdigit()):
            cid = int(category_ref)
            row = self._conn.execute(
                "SELECT id FROM categories WHERE id = ?", (cid,)
            ).fetchone()
            return int(row[0]) if row else None
        name = str(category_ref).strip()
        if not name:
            return None
        row = self._conn.execute(
            """
            SELECT id FROM categories
            WHERE LOWER(name_en) = LOWER(?) OR LOWER(name_ar) = LOWER(?)
            LIMIT 1
            """,
            (name, name),
        ).fetchone()
        return int(row[0]) if row else None

    def _row_to_answer(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["categories"] = self._categories_for_answer(int(d["id"]))
        return d

    def _categories_for_answer(self, answer_id: int) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT c.id, c.name_en, c.name_ar
            FROM categories c
            JOIN answer_categories ac ON ac.category_id = c.id
            WHERE ac.answer_id = ?
            ORDER BY c.name_en
            """,
            (answer_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_answer(self, answer_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT id, title_en, title_ar, question_en, question_ar,
                   answer_en, answer_ar, date_created, date_modified,
                   url_en, url_ar
            FROM answers WHERE id = ?
            """,
            (answer_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_answer(row)

    def fetch_answers_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        cur = self._conn.execute(
            f"""
            SELECT id, title_en, title_ar, question_en, question_ar,
                   answer_en, answer_ar, date_created, date_modified,
                   url_en, url_ar
            FROM answers WHERE id IN ({placeholders})
            """,
            ids,
        )
        by_id = {int(r["id"]): self._row_to_answer(r) for r in cur.fetchall()}
        return [by_id[i] for i in ids if i in by_id]

    def search_answers(
        self,
        query: str,
        *,
        limit: int = 20,
        category_id: int | None = None,
    ) -> list[dict[str, Any]]:
        q = query.strip()
        if len(q) < 2:
            return []
        terms = [t for t in re.split(r"\s+", q) if t]
        if not terms:
            return []

        where_parts: list[str] = []
        params: list[Any] = []
        for term in terms:
            like = _like_term(term)
            where_parts.append(
                """(
                    title_en LIKE ? ESCAPE '\\' OR question_en LIKE ? ESCAPE '\\'
                    OR answer_en LIKE ? ESCAPE '\\'
                    OR title_ar LIKE ? ESCAPE '\\' OR question_ar LIKE ? ESCAPE '\\'
                    OR answer_ar LIKE ? ESCAPE '\\'
                )"""
            )
            params.extend([like] * 6)

        join = ""
        if category_id is not None:
            join = "JOIN answer_categories ac ON ac.answer_id = a.id AND ac.category_id = ?"
            params = [category_id, *params]

        sql = f"""
            SELECT DISTINCT a.id, a.title_en, a.title_ar, a.question_en, a.question_ar,
                   a.answer_en, a.answer_ar, a.date_created, a.date_modified,
                   a.url_en, a.url_ar
            FROM answers a
            {join}
            WHERE {" AND ".join(where_parts)}
            ORDER BY a.date_modified DESC
            LIMIT ?
        """
        params.append(limit)
        cur = self._conn.execute(sql, params)
        out: list[dict[str, Any]] = []
        for row in cur.fetchall():
            d = self._row_to_answer(row)
            en = (d.get("answer_en") or d.get("question_en") or "")[:280]
            d["excerpt_en"] = en
            out.append(d)
        return out

    def fetch_answer_ids_for_category(self, category_id: int) -> set[int]:
        cur = self._conn.execute(
            "SELECT answer_id FROM answer_categories WHERE category_id = ?",
            (category_id,),
        )
        return {int(row[0]) for row in cur.fetchall()}
