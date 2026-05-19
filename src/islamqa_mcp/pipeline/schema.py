"""SQLite schema for islamqa.db."""

from __future__ import annotations

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY,
    name_en    TEXT,
    name_ar    TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id            INTEGER PRIMARY KEY,
    title_en      TEXT,
    title_ar      TEXT,
    question_en   TEXT,
    question_ar   TEXT,
    answer_en     TEXT,
    answer_ar     TEXT,
    date_created  TEXT,
    date_modified TEXT,
    url_en        TEXT,
    url_ar        TEXT,
    embedding     BLOB
);

CREATE TABLE IF NOT EXISTS answer_categories (
    answer_id   INTEGER NOT NULL REFERENCES answers(id),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    PRIMARY KEY (answer_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_modified ON answers(date_modified);
CREATE INDEX IF NOT EXISTS idx_answer_categories_cat ON answer_categories(category_id);
"""


def apply_schema(conn) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
