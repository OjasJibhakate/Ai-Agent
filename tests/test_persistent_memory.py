"""persistent_memory.py: the whole lifecycle through one object, including a restart."""

import json
import os

import pytest

from ragnarok.memory.vault import VaultKeyError
from ragnarok.persistent_memory import PersistentMemory
from tests.fake_embedder import FakeEmbedder
from tests.test_m3_ingest import ScriptedLlm, cand


@pytest.fixture()
def key() -> bytes:
    return os.urandom(32)


def open_pm(tmp_path, key, table=None):
    """Open with the fake embedder and a scripted model; thresholds suit the fake's low cosines."""
    pm = PersistentMemory.open(
        tmp_path / "vault.db", key, embedder=FakeEmbedder(), llm=ScriptedLlm(table or {})
    )
    pm.store.min_semantic = 0.1
    pm.ingestor.sim_floor, pm.ingestor.same_slot = 0.4, 0.7
    return pm


async def test_what_you_tell_it_survives_a_restart(tmp_path, key):
    table = {
        "I prefer meetings after 2pm": [cand("Prefers meetings after 14:00")],
        "My manager is Ravi": [cand("Manager is Ravi", "semantic")],
    }
    pm = open_pm(tmp_path, key, table)
    await pm.observe("I prefer meetings after 2pm")
    await pm.observe("My manager is Ravi")
    report = await pm.learn()
    assert report.added == 2
    assert [h.memory.content for h in await pm.recall("when do I like meetings")] == [
        "Prefers meetings after 14:00"
    ]
    await pm.close()  # the process "dies"

    again = open_pm(tmp_path, key)
    assert [h.memory.content for h in await again.recall("who is my manager")] == [
        "Manager is Ravi"
    ]
    assert "- Prefers meetings after 14:00" in await again.profile()
    stats = await again.stats()
    assert stats["active"] == 2 and stats["events"] == 2 and stats["events_not_yet_learned"] == 0
    await again.close()


async def test_the_vault_needs_the_right_key_and_is_encrypted(tmp_path, key):
    pm = open_pm(tmp_path, key)
    await pm.remember("Dentist is Dr Rao")
    await pm.checkpoint()
    await pm.close()
    raw = (tmp_path / "vault.db").read_bytes()
    assert not raw.startswith(b"SQLite format 3") and b"Dr Rao" not in raw
    with pytest.raises(VaultKeyError):
        PersistentMemory.open(
            tmp_path / "vault.db", os.urandom(32), embedder=FakeEmbedder(), llm=ScriptedLlm({})
        )


async def test_remember_reports_what_happened(tmp_path, key):
    pm = open_pm(tmp_path, key)
    first = await pm.remember("Prefers meetings after 2pm", "preference")
    assert first.action == "ADD"
    same = await pm.remember("prefers meetings after 2pm", "preference")
    assert same.action == "NOOP" and same.memory_id == first.memory_id
    changed = await pm.remember("Prefers meetings after 3pm", "preference")
    assert changed.action == "SUPERSEDE" and changed.memory_id != first.memory_id
    assert [m.content for m in await pm.list()] == ["Prefers meetings after 3pm"]
    await pm.close()


async def test_correct_replaces_a_fact_and_keeps_the_history(tmp_path, key):
    pm = open_pm(tmp_path, key)
    old = (await pm.remember("Lives in Pune", "semantic", importance=0.9)).memory_id
    await pm.pin(old)
    new = await pm.correct(old, "Lives in Bengaluru")
    fresh = await pm.get(new)
    assert fresh.origin == "user_stated" and fresh.source == "correction"
    assert fresh.importance == 0.9 and fresh.pinned  # what mattered about the old one carries over
    assert [m.content for m in await pm.history(new)] == ["Lives in Bengaluru", "Lives in Pune"]
    assert (await pm.get(old)).status == "superseded"
    assert [h.memory.id for h in await pm.recall("Bengaluru lives")] == [new]
    with pytest.raises(ValueError):
        await pm.correct(old, "Lives in Mumbai")  # the old version can no longer be corrected
    await pm.close()


async def test_revert_brings_the_older_fact_back(tmp_path, key):
    pm = open_pm(tmp_path, key)
    old = (await pm.remember("Gym day is Tuesday", "preference")).memory_id
    new = await pm.correct(old, "Gym day is Thursday")
    archived = await pm.revert(old)
    assert archived == new
    assert (await pm.get(old)).status == "active" and (await pm.get(old)).valid_to is None
    assert (await pm.get(new)).status == "archived"  # kept, not deleted
    assert [h.memory.id for h in await pm.recall("gym day")] == [old]
    assert len(pm.store.index) == 1
    await pm.restore(new)  # the user can still choose to have both visible again
    assert (await pm.get(new)).status == "active"
    await pm.close()


async def test_revert_only_works_one_step_at_a_time(tmp_path, key):
    pm = open_pm(tmp_path, key)
    v1 = (await pm.remember("Sprint length is two weeks", "semantic")).memory_id
    v2 = await pm.correct(v1, "Sprint length is three weeks")
    v3 = await pm.correct(v2, "Sprint length is four weeks")
    with pytest.raises(ValueError, match="newest"):
        await pm.revert(v1)  # v2 is itself replaced; undo the newest first
    await pm.revert(v2)
    assert (await pm.get(v2)).status == "active" and (await pm.get(v3)).status == "archived"
    with pytest.raises(ValueError):
        await pm.revert(v3)  # not superseded
    await pm.close()


async def test_pin_unpin_and_profile_order(tmp_path, key):
    pm = open_pm(tmp_path, key)
    a = (await pm.remember("Prefers dark mode", "preference", importance=0.3)).memory_id
    b = (await pm.remember("Call me Sam", "semantic", importance=0.5)).memory_id
    assert (await pm.profile()).splitlines()[0] == "- Prefers dark mode"
    await pm.pin(b)
    assert (await pm.profile()).splitlines()[0] == "- Call me Sam"  # pinned first
    with pytest.raises(ValueError):
        await pm.archive(b)  # pinned memories are never archived
    await pm.unpin(b)
    await pm.archive(b)
    assert "Call me Sam" not in await pm.profile()
    with pytest.raises(ValueError):
        await pm.pin(b)  # archived: not active
    assert a
    await pm.close()


async def test_forget_removes_everything_and_says_so_twice(tmp_path, key):
    pm = open_pm(tmp_path, key)
    ev = await pm.observe("my dentist is Dr Zorblatt")
    mid = (await pm.remember("Dentist is Dr Zorblatt", "semantic", event_ids=(ev,))).memory_id
    assert await pm.forget(mid) is True and await pm.forget(mid) is False
    assert await pm.recall("Zorblatt") == [] and await pm.get(mid) is None
    assert pm.vault.query("SELECT action, ref_id FROM audit_log") == [("memory_deleted", mid)]
    await pm.close()


async def test_export_is_refused_unless_the_user_accepts_plain_text(tmp_path, key):
    pm = open_pm(tmp_path, key)
    old = (await pm.remember("Lives in Pune", "semantic")).memory_id
    await pm.correct(old, "Lives in Bengaluru")
    target = tmp_path / "out.jsonl"
    with pytest.raises(PermissionError, match=r"not encrypted|readable text"):
        await pm.export(target)
    assert not target.exists()
    count = await pm.export(target, allow_plaintext=True)
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert count == 2 and [r["content"] for r in rows] == ["Lives in Pune", "Lives in Bengaluru"]
    assert rows[0]["status"] == "superseded" and rows[1]["supersedes_id"] == rows[0]["id"]
    assert {"event_ids", "entities", "origin", "confidence"} <= rows[0].keys()
    await pm.close()


async def test_stats_track_the_lifecycle(tmp_path, key):
    pm = open_pm(tmp_path, key, {"I like tea": [cand("Likes tea")]})
    assert (await pm.stats())["events_not_yet_learned"] == 0
    await pm.observe("I like tea")
    assert (await pm.stats())["events_not_yet_learned"] == 1
    await pm.learn()
    s = await pm.stats()
    assert s["events_not_yet_learned"] == 0 and s["active"] == 1 and s["indexed_vectors"] == 1
    a = (await pm.remember("Prefers dark mode", "preference")).memory_id
    await pm.correct(a, "Prefers light mode")
    s = await pm.stats()
    assert s["active"] == 2 and s["superseded"] == 1 and s["by_type"]["preference"] == 2
    await pm.close()


async def test_secrets_are_scrubbed_before_anything_is_stored(tmp_path, key):
    pm = open_pm(tmp_path, key)
    ev = await pm.observe("my password is hunter22 and my token=abcd1234")
    payload = pm.vault.query("SELECT payload FROM events WHERE id = ?", (ev,))[0][0]
    assert "hunter22" not in payload and "abcd1234" not in payload
    assert await pm.observe("ok") is None  # trivial text is not stored
    await pm.close()


def test_the_file_explains_itself():
    import ragnarok.persistent_memory as module

    doc = module.__doc__
    for section in (
        "TWO TIERS",
        "WRITING",
        "READING",
        "CHANGING YOUR MIND",
        "DURABILITY",
        "KEEPING IT TIDY",
        "LIMITS",
    ):
        assert section in doc
