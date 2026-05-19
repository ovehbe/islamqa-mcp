"""In-memory embedding matrix for semantic search."""

from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path

import numpy as np


class EmbeddingIndex:
    __slots__ = ("ids", "mat")

    def __init__(self, ids: np.ndarray, mat: np.ndarray) -> None:
        self.ids = np.asarray(ids, dtype=np.int64)
        self.mat = np.asarray(mat, dtype=np.float32)

    @classmethod
    def load(cls, db_path: Path) -> EmbeddingIndex:
        resolved = db_path.expanduser().resolve()
        conn = sqlite3.connect(str(resolved), check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        try:
            cur = conn.execute(
                """
                SELECT a.id, a.embedding
                FROM answers a
                WHERE a.embedding IS NOT NULL
                ORDER BY a.id
                """
            )
            ids_list: list[int] = []
            mats: list[np.ndarray] = []
            for row in cur:
                blob = row[1]
                if not blob:
                    continue
                arr = pickle.loads(blob)  # noqa: S301
                arr = np.asarray(arr, dtype=np.float32).ravel()
                ids_list.append(int(row[0]))
                mats.append(arr)
        finally:
            conn.close()
        if not mats:
            raise RuntimeError("No embeddings found in answers.embedding")
        mat = np.stack(mats, axis=0)
        return cls(np.array(ids_list, dtype=np.int64), mat)

    def topk(
        self,
        query_vec: np.ndarray,
        k: int,
    ) -> list[tuple[int, float]]:
        q = np.asarray(query_vec, dtype=np.float32).ravel()
        if q.shape[0] != self.mat.shape[1]:
            raise ValueError(
                f"Query dim {q.shape[0]} does not match index dim {self.mat.shape[1]}"
            )
        nq = float(np.linalg.norm(q))
        if nq > 0:
            q = q / nq
        sims = self.mat @ q
        k = max(1, min(int(k), int(sims.size)))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        out: list[tuple[int, float]] = []
        for i in idx:
            sc = float(sims[i])
            if not np.isfinite(sc):
                continue
            out.append((int(self.ids[i]), sc))
        return out

    def topk_filtered(
        self,
        query_vec: np.ndarray,
        k: int,
        *,
        allowed_ids: set[int] | None,
    ) -> list[tuple[int, float]]:
        """Top-k restricted to a set of answer IDs (e.g. category filter)."""
        if not allowed_ids:
            return []
        q = np.asarray(query_vec, dtype=np.float32).ravel()
        nq = float(np.linalg.norm(q))
        if nq > 0:
            q = q / nq
        sims = self.mat @ q
        id_to_idx = {int(aid): i for i, aid in enumerate(self.ids)}
        masked = np.full(len(sims), -np.inf, dtype=np.float32)
        for aid in allowed_ids:
            i = id_to_idx.get(aid)
            if i is not None:
                masked[i] = sims[i]
        k = max(1, min(int(k), len(masked)))
        idx = np.argpartition(-masked, k - 1)[:k]
        idx = idx[np.argsort(-masked[idx])]
        out: list[tuple[int, float]] = []
        for i in idx:
            sc = float(masked[i])
            if not np.isfinite(sc) or sc <= -1e9:
                continue
            out.append((int(self.ids[i]), sc))
        return out
