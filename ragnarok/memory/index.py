"""Vector indexes behind one small interface (docs/MEMORY.md section 8).

The index is derived data: it can always be rebuilt from the `embeddings` table. Vectors are
unit-normalised on the way in, so scores are cosine similarities.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

import numpy as np
from turbovec import IdMapIndex


class VectorIndex(Protocol):
    def add(self, ids: np.ndarray, vectors: np.ndarray) -> None: ...
    def remove(self, ids: Iterable[int]) -> None: ...
    def search(
        self, query: np.ndarray, k: int, allow: set[int] | None = None
    ) -> list[tuple[int, float]]: ...
    def __len__(self) -> int: ...


def normalize(vectors: np.ndarray) -> np.ndarray:
    v = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(norms == 0, 1.0, norms)


class ExactIndex:
    """Brute-force cosine search over a float32 matrix. The baseline and the fallback."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._ids = np.empty(0, dtype=np.uint64)
        self._mat = np.empty((0, dim), dtype=np.float32)

    def add(self, ids: np.ndarray, vectors: np.ndarray) -> None:
        ids = np.asarray(ids, dtype=np.uint64)
        self.remove(ids.tolist())  # re-adding an id replaces it
        self._ids = np.concatenate([self._ids, ids])
        self._mat = np.concatenate([self._mat, normalize(vectors)])

    def remove(self, ids: Iterable[int]) -> None:
        drop = np.isin(self._ids, np.fromiter(ids, dtype=np.uint64))
        if drop.any():
            self._ids, self._mat = self._ids[~drop], self._mat[~drop]

    def search(
        self, query: np.ndarray, k: int, allow: set[int] | None = None
    ) -> list[tuple[int, float]]:
        ids, mat = self._ids, self._mat
        if allow is not None:
            keep = np.isin(ids, np.fromiter(allow, dtype=np.uint64))
            ids, mat = ids[keep], mat[keep]
        if len(ids) == 0:
            return []
        scores = mat @ normalize(query)
        top = np.argsort(-scores)[:k]
        return [(int(ids[i]), float(scores[i])) for i in top]

    def __len__(self) -> int:
        return len(self._ids)


class TurbovecIndex:
    """Compressed (TurboQuant-style, 4-bit) index. Serializable into the vault (D-020)."""

    def __init__(self, dim: int, bit_width: int = 4, *, _inner: IdMapIndex | None = None) -> None:
        self.dim = dim
        self.bit_width = bit_width
        self._idx = _inner if _inner is not None else IdMapIndex(dim=dim, bit_width=bit_width)

    def add(self, ids: np.ndarray, vectors: np.ndarray) -> None:
        ids = np.asarray(ids, dtype=np.uint64)
        self.remove(i for i in ids.tolist() if self._idx.contains(i))
        self._idx.add_with_ids(normalize(vectors), ids)

    def remove(self, ids: Iterable[int]) -> None:
        for i in ids:
            self._idx.remove(int(i))

    def search(
        self, query: np.ndarray, k: int, allow: set[int] | None = None
    ) -> list[tuple[int, float]]:
        if len(self._idx) == 0 or (allow is not None and not allow):
            return []
        kwargs = {}
        if allow is not None:
            kwargs["allowlist"] = np.fromiter(allow, dtype=np.uint64)
        scores, ids = self._idx.search(normalize(query)[None, :], k=k, **kwargs)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0], strict=True)]

    def __len__(self) -> int:
        return len(self._idx)

    def to_bytes(self) -> bytes:
        return self._idx.to_bytes()

    @classmethod
    def from_bytes(cls, data: bytes) -> TurbovecIndex:
        inner = IdMapIndex.from_bytes(data)
        return cls(inner.dim, inner.bit_width, _inner=inner)
