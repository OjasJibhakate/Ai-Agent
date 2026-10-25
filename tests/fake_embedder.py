"""Deterministic stand-in for nomic-embed-text: hashed bag of words (word overlap ~ similarity)."""

import hashlib
import re

import numpy as np

DIM = 768
_PREFIX = re.compile(r"^search_(query|document): ")


def _word_vector(word: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "little")
    return np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)


def embed_text(text: str) -> np.ndarray:
    words = re.findall(r"\w+", _PREFIX.sub("", text).lower())
    vec = sum((_word_vector(w) for w in words), np.zeros(DIM, dtype=np.float32))
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.down = False

    async def embed(self, texts: list[str]) -> np.ndarray:
        from ragnarok.runtime.gateway import GatewayError

        if self.down:
            raise GatewayError("embedder down")
        self.calls += len(texts)
        return np.stack([embed_text(t) for t in texts])
