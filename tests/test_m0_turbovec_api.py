"""M0: Turbovec exposes the API the memory design needs (docs/MEMORY.md section 8).

Needs: external uint64 ids, remove(id), allowlist search, bytes round-trip (so the index can be
stored inside the encrypted vault instead of as a plaintext .tvim file).
"""

import numpy as np
import pytest
from turbovec import IdMapIndex

DIM = 768  # nomic-embed-text; must be a multiple of 8


def _unit(rng, n):
    v = rng.standard_normal((n, DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture()
def index_and_vectors():
    rng = np.random.default_rng(0)
    vecs = _unit(rng, 500)
    ids = np.arange(1000, 1500, dtype=np.uint64)  # stable memory ids, not 0..n
    idx = IdMapIndex(dim=DIM, bit_width=4)
    idx.add_with_ids(vecs, ids)
    return idx, vecs, ids


def test_self_query_returns_own_id(index_and_vectors):
    idx, vecs, ids = index_and_vectors
    _, got = idx.search(vecs[:20], k=1)
    assert (got[:, 0] == ids[:20]).mean() >= 0.95


def test_contains_and_remove(index_and_vectors):
    idx, vecs, _ = index_and_vectors
    assert idx.contains(1007)
    assert idx.remove(1007) is True
    assert not idx.contains(1007)
    _, got = idx.search(vecs[7:8], k=10)
    assert 1007 not in got[0].tolist()
    assert idx.remove(1007) is False  # already gone


def test_allowlist_restricts_results(index_and_vectors):
    idx, vecs, ids = index_and_vectors
    allow = ids[100:150]
    _, got = idx.search(vecs[:5], k=10, allowlist=allow)
    assert set(got.flatten().tolist()) <= set(allow.tolist())


def test_bytes_round_trip_preserves_results(index_and_vectors):
    idx, vecs, _ = index_and_vectors
    blob = idx.to_bytes()
    assert isinstance(blob, bytes) and len(blob) > 0
    restored = IdMapIndex.from_bytes(blob)
    s1, i1 = idx.search(vecs[:10], k=5)
    s2, i2 = restored.search(vecs[:10], k=5)
    assert np.array_equal(i1, i2)
    assert np.allclose(s1, s2)


def test_serialized_size_is_compact_per_vector():
    """Measured 2026-10-25: ~0.86 MB fixed overhead at 4-bit, then ~396 B/vector (ideal 384)."""
    rng = np.random.default_rng(2)

    def size(n):
        idx = IdMapIndex(dim=DIM, bit_width=4)
        idx.add_with_ids(_unit(rng, n), np.arange(n, dtype=np.uint64))
        return len(idx.to_bytes())

    marginal = (size(5000) - size(500)) / 4500
    assert marginal < 450  # float32 is 3072 B/vector
    assert size(5000) < 5000 * DIM * 4 / 5  # >5x smaller overall once past the fixed overhead
