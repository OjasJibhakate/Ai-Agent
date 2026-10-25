"""Supersede, reinforce, archive/restore, hard delete, and the raw event tier."""

import pytest

from tests.fake_embedder import embed_text
from tests.test_m2_memory import Clock, key, kind, make  # noqa: F401  (fixtures)


def vec(text: str):
    return embed_text(text)


async def test_supersede_keeps_history_and_hides_the_old_fact(tmp_path, key, kind):  # noqa: F811
    clock = Clock()
    vault, store, svc, _ = make(tmp_path, key, kind, clock)
    old = await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    clock.advance(days=3)
    new = store.add_memory(
        "Prefers meetings after 15:00 now",
        vec("meetings after 15:00 prefers"),
        memory_type="preference",
        source="chat",
        supersedes=old,
    )
    old_row, new_row = store.get(old), store.get(new)
    assert old_row.status == "superseded" and old_row.valid_to == "2026-10-04T09:00:00Z"
    assert new_row.supersedes_id == old and new_row.status == "active"
    assert [m.id for m in store.history(new)] == [new, old]
    assert [h.memory.id for h in await svc.recall("meetings after")] == [new]
    assert len(store.index) == 1
    vault.close()


async def test_cannot_supersede_a_non_active_memory(tmp_path, key, kind):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, kind)
    old = await svc.remember("Fact one", "semantic", "chat")
    store.archive(old)
    with pytest.raises(ValueError):
        store.add_memory(
            "Fact two", vec("two"), memory_type="semantic", source="chat", supersedes=old
        )
    assert vault.query("SELECT count(*) FROM memories") == [
        (1,)
    ]  # rolled back with the failed update
    vault.close()


async def test_reinforce_bumps_confidence_but_caps_inferred(tmp_path, key, kind):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, kind)
    stated = await svc.remember("Likes green tea", "preference", "chat")
    inferred = await svc.remember("Likes oat milk", "preference", "calendar", origin="inferred")
    for _ in range(5):
        store.reinforce(stated)
        store.reinforce(inferred)
    assert store.get(stated).confidence == pytest.approx(0.99)
    assert store.get(inferred).confidence == pytest.approx(
        0.7
    )  # repetition never upgrades inference
    assert store.get(stated).access_count == 5
    vault.close()


async def test_archive_restore_round_trip_and_pinned_guard(tmp_path, key, kind):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, kind)
    a = await svc.remember("Old trivia about walruses", "semantic", "chat")
    p = await svc.remember("Never share my address", "preference", "chat", pinned=True)
    store.archive(a)
    assert await svc.recall("walruses") == [] and len(store.index) == 1
    store.restore(a)
    assert [h.memory.id for h in await svc.recall("walruses")] == [a]
    with pytest.raises(ValueError):
        store.archive(p)
    with pytest.raises(ValueError):
        store.restore(a)  # already active
    vault.close()


async def test_hard_delete_removes_every_trace(tmp_path, key, kind):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, kind)
    ev = store.record_event("chat", "user_message", "my dentist is Dr Zorblatt")
    doomed = await svc.remember(
        "Dentist is Dr Zorblatt", "semantic", "chat", entities=[("Zorblatt", "person")],
        tags=["health"], event_ids=[ev],
    )  # fmt: skip
    keep = await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    assert store.delete(doomed) is True
    assert store.delete(doomed) is False

    assert store.get(doomed) is None
    assert await svc.recall("Zorblatt dentist") == []
    assert vault.query("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'zorblatt'") == [
        (0,)
    ]
    for table in ("embeddings", "memory_tags", "memory_entities", "memory_sources"):
        assert vault.query(f"SELECT count(*) FROM {table} WHERE memory_id = ?", (doomed,)) == [(0,)]
    assert len(store.index) == 1
    tomb = vault.query("SELECT action, ref_id FROM audit_log")
    assert tomb == [("memory_deleted", doomed)]  # id and action only, no content
    assert [h.memory.id for h in await svc.recall("meetings")] == [keep]

    store.checkpoint_index()
    vault.checkpoint()
    path = vault.path
    vault.close()
    # also gone from the (decrypted) pages: reopen and scan the database content
    from ragnarok.memory.vault import Vault

    with Vault.open(path, key) as v2:
        dump = " ".join(str(r) for r in v2.query("SELECT * FROM audit_log"))
        assert "Zorblatt" not in dump
        assert v2.query("SELECT count(*) FROM events WHERE payload LIKE '%Zorblatt%'") == [(1,)]
        # the raw event tier is separate: deleting a memory does not silently edit the event log


async def test_delete_survives_restart_without_checkpoint(tmp_path, key, kind):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, kind)
    gone = await svc.remember("Temporary codeword pineapple", "semantic", "chat")
    await svc.remember("Prefers meetings after 14:00", "preference", "chat")
    store.checkpoint_index()
    store.delete(gone)
    vault.close()
    vault2, store2, svc2, _ = make(tmp_path, key, kind)
    assert await svc2.recall("pineapple") == []
    assert len(store2.index) == 1
    vault2.close()


def test_events_scrub_secrets_and_drop_trivia(tmp_path, key):  # noqa: F811
    vault, store, _, _ = make(tmp_path, key, "exact")
    assert store.record_event("chat", "user_message", "  ") is None
    assert store.record_event("chat", "user_message", "ok") is None
    eid = store.record_event(
        "chat", "user_message",
        "my password is hunter22 and key AKIAABCDEFGHIJKLMNOP and token=abcd1234",
    )  # fmt: skip
    text = vault.query("SELECT payload FROM events WHERE id = ?", (eid,))[0][0]
    assert "hunter22" not in text and "AKIA" not in text and "abcd1234" not in text
    assert text.count("[REDACTED]") == 3
    vault.close()


def test_event_dedupe_window_and_pending_flow(tmp_path, key):  # noqa: F811
    clock = Clock()
    vault, store, _, _ = make(tmp_path, key, "exact", clock)
    a = store.record_event("chat", "user_message", "I moved to Pune last week")
    assert store.record_event("chat", "user_message", "I moved to Pune last week") == a
    clock.advance(seconds=301)
    b = store.record_event("chat", "user_message", "I moved to Pune last week")
    assert b != a
    assert [e["id"] for e in store.pending_events()] == [a, b]
    store.mark_ingested([a])
    assert [e["id"] for e in store.pending_events()] == [b]
    vault.close()


async def test_expire_events_keeps_cited_provenance(tmp_path, key):  # noqa: F811
    clock = Clock()
    vault, store, svc, _ = make(tmp_path, key, "exact", clock)
    cited = store.record_event(
        "chat", "user_message", "I am allergic to penicillin", retention_days=1
    )
    loose = store.record_event("chat", "user_message", "what a nice day today", retention_days=1)
    await svc.remember("Allergic to penicillin", "semantic", "chat", event_ids=[cited])
    clock.advance(days=2)
    assert store.expire_events() == 1
    ids = [r[0] for r in vault.query("SELECT id FROM events")]
    assert ids == [cited] and loose not in ids
    vault.close()


async def test_profile_orders_pinned_then_preferences_and_hides_secret(tmp_path, key):  # noqa: F811
    vault, store, svc, _ = make(tmp_path, key, "exact")
    await svc.remember("Project payments uses Python", "project", "chat", importance=0.4)
    await svc.remember("Prefers dark mode", "preference", "chat", importance=0.3)
    await svc.remember("Prefers meetings after 14:00", "preference", "chat", importance=0.9)
    await svc.remember("Pinned: call me Sam", "semantic", "chat", pinned=True)
    await svc.remember("Secret pref", "preference", "chat", sensitivity="secret")
    await svc.remember("A dentist visit happened", "episodic", "chat")
    got = [m.content for m in store.profile_memories()]
    assert got == [
        "Pinned: call me Sam",
        "Prefers meetings after 14:00",
        "Prefers dark mode",
        "Project payments uses Python",
    ]
    vault.close()
