"""M2: remember/recall with FTS5 + vector index + RRF + rerank, and the restart test."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from ragnarok.memory.index import ExactIndex, TurbovecIndex
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore, fts_query
from ragnarok.memory.vault import Vault
from tests.fake_embedder import FakeEmbedder, embed_text

NOISE = 0.1  # hashed bag-of-words vectors: unrelated pairs score about 0.03
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture()
def key() -> bytes:
    return os.urandom(32)


@pytest.fixture(params=["turbovec", "exact"])
def kind(request) -> str:
    return request.param


def make(tmp_path, key, kind, clock=None) -> tuple[Vault, MemoryStore, MemoryService, FakeEmbedder]:
    vault = Vault.open(tmp_path / "vault.db", key)
    store = MemoryStore(vault, index_kind=kind, clock=clock or Clock(), min_semantic=NOISE)
    store.start()
    emb = FakeEmbedder()
    return vault, store, MemoryService(store, emb), emb


def test_fts_query_is_safe_and_drops_stopwords():
    assert fts_query("What is the port?") == '"port"'
    assert fts_query('weird "quote" AND (paren) NEAR*') == '"weird" OR "quote" OR "paren" OR "near"'
    assert fts_query("the of a") is None


async def test_remember_then_recall_both_signals(tmp_path, key, kind):
    vault, _store, svc, _ = make(tmp_path, key, kind)
    await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    await svc.remember(
        "payments-service test_refund_rounding is failing on port 8089", "project", "tool"
    )
    await svc.remember("Dentist appointment on 3 November", "episodic", "chat")

    hits = await svc.recall("when should I schedule meetings")
    assert hits[0].memory.content == "Prefers meetings after 14:00"
    assert hits[0].lexical_rank == 1 and hits[0].semantic_rank == 1

    # an exact identifier is found by FTS5
    hits = await svc.recall("8089")
    assert hits[0].memory.memory_type == "project" and hits[0].lexical_rank == 1
    vault.close()


async def test_exact_duplicate_is_not_stored_twice(tmp_path, key, kind):
    vault, _, svc, _ = make(tmp_path, key, kind)
    a = await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    b = await svc.remember("  prefers   MEETINGS after 14:00 ", "preference", "chat")
    assert a == b
    assert vault.query("SELECT count(*) FROM memories") == [(1,)]
    vault.close()


async def test_origin_sets_confidence_and_inferred_ranks_lower(tmp_path, key, kind):
    vault, _, svc, _ = make(tmp_path, key, kind)
    stated = await svc.remember("User likes tea in the morning", "preference", "chat")
    inferred = await svc.remember(
        "User likes tea in the morning", "preference", "calendar", origin="inferred"
    )
    # same normalized text -> deduplicated, so use different wording of equal relevance
    assert stated == inferred
    other = await svc.remember(
        "User likes tea in the evening", "preference", "calendar", origin="inferred"
    )
    hits = await svc.recall("tea")
    conf = {h.memory.id: h.memory.confidence for h in hits}
    assert conf[stated] == 0.95 and conf[other] == 0.7
    assert hits[0].memory.id == stated
    vault.close()


async def test_importance_and_pinned_stored(tmp_path, key, kind):
    vault, _, svc, _ = make(tmp_path, key, kind)
    low = await svc.remember("Project alpha uses postgres", "project", "chat", importance=0.1)
    high = await svc.remember(
        "Project beta uses postgres", "project", "chat", importance=0.9, pinned=True
    )
    hits = await svc.recall("postgres")
    assert [h.memory.id for h in hits] == [high, low]
    assert hits[0].memory.pinned is True
    vault.close()


async def test_entity_overlap_and_project_boost(tmp_path, key, kind):
    vault, _, svc, _ = make(tmp_path, key, kind)
    await svc.remember("Meeting notes recorded", "episodic", "chat")
    target = await svc.remember(
        "Meeting notes recorded for review", "episodic", "chat", entities=[("Priya", "person")]
    )
    hits = await svc.recall("meeting notes with Priya")
    assert hits[0].memory.id == target

    a = await svc.remember("Deploy checklist alpha", "project", "chat")
    b = await svc.remember("Deploy checklist beta", "project", "chat", project="payments")
    hits = await svc.recall("deploy checklist", project="payments")
    assert hits[0].memory.id == b and a in [h.memory.id for h in hits]
    vault.close()


async def test_time_words_favor_recent_memories(tmp_path, key, kind):
    clock = Clock()
    vault, _, svc, _ = make(tmp_path, key, kind, clock)
    old = await svc.remember("Standup notes: discussed the release", "episodic", "chat")
    clock.advance(days=20)
    new = await svc.remember("Standup notes: discussed the migration", "episodic", "chat")
    hits = await svc.recall("standup notes from yesterday")
    assert hits[0].memory.id == new and old in [h.memory.id for h in hits]
    vault.close()


async def test_non_active_and_secret_memories_never_surface(tmp_path, key, kind):
    vault, _store, svc, _ = make(tmp_path, key, kind)
    stale = await svc.remember("Old wifi password hint banana", "semantic", "chat")
    secret = await svc.remember(
        "Vault recovery code banana", "semantic", "chat", sensitivity="secret"
    )
    live = await svc.remember("Banana bread recipe", "semantic", "chat")
    with vault.transaction() as con:  # archived in SQLite while the index still holds its vector
        con.execute("UPDATE memories SET status = 'archived' WHERE id = ?", (stale,))
    ids = [h.memory.id for h in await svc.recall("banana")]
    assert ids == [live]
    assert secret not in ids and stale not in ids
    vault.close()


async def test_embedder_down_falls_back_to_lexical(tmp_path, key, kind):
    vault, _, svc, emb = make(tmp_path, key, kind)
    await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    emb.down = True
    hits = await svc.recall("meetings")
    assert len(hits) == 1 and hits[0].semantic_rank is None and hits[0].lexical_rank == 1
    vault.close()


async def test_recall_bumps_access_count(tmp_path, key, kind):
    vault, store, svc, _ = make(tmp_path, key, kind)
    mid = await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    await svc.recall("meetings")
    await svc.flush()
    mem = store.get(mid)
    assert mem is not None and mem.access_count == 1 and mem.last_accessed_at is not None
    vault.close()


async def test_restart_recalls_everything(tmp_path, key, kind):
    vault, _store, svc, _ = make(tmp_path, key, kind)
    facts = [f"Fact number {i} about topic{i} and widget{i}" for i in range(40)]
    for f in facts:
        await svc.remember(f, "semantic", "chat")
    vault.close()  # the "kill": nothing was checkpointed, the index is gone with the process

    vault2, store2, svc2, _ = make(tmp_path, key, kind)
    assert len(store2.index) == 40
    for i in (0, 17, 39):
        hits = await svc2.recall(f"topic{i} widget{i}")
        assert hits[0].memory.content == facts[i]
    vault2.close()


async def test_restart_loads_checkpointed_index_and_replays_later_writes(tmp_path, key):
    vault, store, svc, _ = make(tmp_path, key, "turbovec")
    for i in range(20):
        await svc.remember(f"Early fact {i} about gadget{i}", "semantic", "chat")
    store.checkpoint_index()
    late = await svc.remember("Late fact about zeppelin", "semantic", "chat")  # after checkpoint
    vault.close()

    vault2 = Vault.open(tmp_path / "vault.db", key)
    store2 = MemoryStore(vault2, clock=Clock(), min_semantic=NOISE)
    assert store2.start() == "loaded"  # blob loaded, outbox entries after it replayed
    assert len(store2.index) == 21
    svc2 = MemoryService(store2, FakeEmbedder())
    assert (await svc2.recall("zeppelin"))[0].memory.id == late
    vault2.close()


async def test_corrupt_index_blob_triggers_rebuild(tmp_path, key):
    vault, store, svc, _ = make(tmp_path, key, "turbovec")
    mid = await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    store.checkpoint_index()
    vault.execute("UPDATE index_blobs SET data = ?", (b"garbage",))
    vault.close()

    vault2 = Vault.open(tmp_path / "vault.db", key)
    store2 = MemoryStore(vault2, clock=Clock(), min_semantic=NOISE)
    assert store2.start() == "rebuilt"
    svc2 = MemoryService(store2, FakeEmbedder())
    assert (await svc2.recall("meetings"))[0].memory.id == mid
    vault2.close()


async def test_stale_blob_count_mismatch_triggers_rebuild(tmp_path, key):
    vault, store, svc, _ = make(tmp_path, key, "turbovec")
    await svc.remember("First fact about apples", "semantic", "chat")
    store.checkpoint_index()
    second = await svc.remember("Second fact about pears", "semantic", "chat")
    vault.execute(
        "DELETE FROM index_outbox"
    )  # simulate a lost outbox: only the count can reveal it
    vault.close()

    vault2 = Vault.open(tmp_path / "vault.db", key)
    store2 = MemoryStore(vault2, clock=Clock(), min_semantic=NOISE)
    assert store2.start() == "rebuilt"
    assert (await MemoryService(store2, FakeEmbedder()).recall("pears"))[0].memory.id == second
    vault2.close()


def test_turbovec_matches_exact_on_top_hit():
    import numpy as np

    rng = np.random.default_rng(1)
    vecs = rng.standard_normal((300, 768)).astype(np.float32)
    ids = np.arange(1, 301, dtype=np.uint64)
    exact, tv = ExactIndex(768), TurbovecIndex(768)
    exact.add(ids, vecs)
    tv.add(ids, vecs)
    agree = 0
    for q in vecs[:50]:
        q = q + 0.2 * rng.standard_normal(768).astype(np.float32)
        agree += exact.search(q, 1)[0][0] == tv.search(q, 1)[0][0]
    assert agree >= 47


def test_embed_text_ignores_prefix():
    import numpy as np

    assert np.allclose(embed_text("search_query: tea"), embed_text("search_document: tea"))
