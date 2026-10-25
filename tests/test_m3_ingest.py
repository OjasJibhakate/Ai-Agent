"""M3: events -> memories with ADD / UPDATE / SUPERSEDE / NOOP, provenance, and a 14-day timeline.

The model is scripted (see ScriptedLlm), so these tests check the pipeline's rules, not the
quality of qwen3.5:4b. Real-model quality is measured by bench/ingest_eval.py.
"""

import re

import pytest

from ragnarok.memory.ingest import DECIDE_SCHEMA, EXTRACT_SCHEMA, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.runtime.gateway import GatewayError
from tests.fake_embedder import FakeEmbedder
from tests.test_m2_memory import Clock, key, make  # noqa: F401  (fixture)


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class ScriptedLlm:
    """Extraction from a table keyed by event text; decisions from a simple overlap rule."""

    def __init__(self, table: dict[str, list[dict]]) -> None:
        self.table = table
        self.extract_calls = 0
        self.decide_calls = 0
        self.seen_prompts: list[str] = []
        self.fail = False
        self.force_decision: dict | None = None

    async def json(self, messages, schema):
        if self.fail:
            raise GatewayError("model down")
        user = messages[-1]["content"]
        self.seen_prompts.append(user)
        if schema is EXTRACT_SCHEMA:
            self.extract_calls += 1
            out = []
            for line in user.splitlines():
                m = re.match(r"\[e\d+ \w+ [^\]]+\] (.*)", line)
                if m:
                    out += self.table.get(m.group(1), [])
            return {"memories": out}
        assert schema is DECIDE_SCHEMA
        self.decide_calls += 1
        if self.force_decision:
            return self.force_decision
        cand = re.search(r"<candidate>\n(.*?)\n</candidate>", user, re.S).group(1)
        stored = re.findall(r"\[m:(\d+) [^\]]+\] (.*)", user)
        for mid, text in stored:
            if words(cand) <= words(text):
                return {"action": "UPDATE", "target_id": int(mid)}
        for mid, text in stored:
            if words(cand) & {"meetings", "manager"} & words(text):
                return {"action": "SUPERSEDE", "target_id": int(mid)}
        return {"action": "ADD", "target_id": None}


def cand(content, mtype="preference", origin="user_stated", **kw):
    return {"content": content, "memory_type": mtype, "origin": origin, **kw}


def setup(tmp_path, key, table, clock=None):  # noqa: F811
    vault, store, _, emb = make(tmp_path, key, "exact", clock)
    llm = ScriptedLlm(table)
    ing = Ingestor(store, emb, llm, sim_floor=0.4)
    return vault, store, llm, ing


async def test_extract_adds_memories_with_provenance_and_entities(tmp_path, key):  # noqa: F811
    text = "My sister Meera lives in Bangalore"
    table = {
        text: [
            cand(
                "Sister Meera lives in Bangalore",
                "semantic",
                entities=[{"name": "Meera", "kind": "person"}],
            )
        ]
    }
    vault, store, _llm, ing = setup(tmp_path, key, table)
    ev = store.record_event("chat", "user_message", text)
    report = await ing.ingest_pending()
    assert (report.events, report.candidates, report.added) == (1, 1, 1)
    mem = store.list_memories()[0]
    assert mem.origin == "user_stated" and mem.confidence == 0.95 and mem.source == "ingest"
    assert vault.query("SELECT event_id FROM memory_sources WHERE memory_id = ?", (mem.id,)) == [
        (ev,)
    ]
    assert vault.query(
        "SELECT e.name FROM memory_entities me JOIN entities e ON e.id = me.entity_id"
    ) == [("Meera",)]
    assert store.pending_events() == []
    vault.close()


async def test_second_run_does_nothing(tmp_path, key):  # noqa: F811
    text = "I take the metro to work"
    vault, store, llm, ing = setup(tmp_path, key, {text: [cand("Commutes by metro")]})
    store.record_event("chat", "user_message", text)
    await ing.ingest_pending()
    again = await ing.ingest_pending()
    assert again.events == 0 and llm.extract_calls == 1
    vault.close()


async def test_origin_is_clamped_to_the_evidence(tmp_path, key):  # noqa: F811
    table = {
        "assistant guessed this": [cand("User probably likes jazz", origin="user_stated")],
        "ci job output": [cand("The build is red", "project", origin="user_stated")],
    }
    vault, store, _llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", "hello there friend")  # makes the batch eligible
    store.record_event("tool", "tool_result", "ci job output")
    await ing.ingest_pending()
    got = {m.content: m.origin for m in store.list_memories()}
    assert got["The build is red"] == "user_stated"  # the batch did contain a user message
    vault.close()

    # batch with only a tool result: user_stated is not allowed
    vault2, store2, _, ing2 = setup(tmp_path / "b", key, table)
    store2.record_event("tool", "tool_result", "ci job output")
    await ing2.ingest_pending()
    assert store2.list_memories()[0].origin == "observed"
    vault2.close()


async def test_assistant_only_batches_skip_the_model(tmp_path, key):  # noqa: F811
    vault, store, llm, ing = setup(tmp_path, key, {})
    store.record_event("chat", "assistant_message", "I think you would enjoy jazz")
    report = await ing.ingest_pending()
    assert llm.extract_calls == 0 and report.candidates == 0 and store.pending_events() == []
    vault.close()


async def test_model_down_leaves_events_pending_then_retries(tmp_path, key):  # noqa: F811
    text = "I am allergic to penicillin"
    vault, store, llm, ing = setup(
        tmp_path, key, {text: [cand("Allergic to penicillin", "semantic")]}
    )
    store.record_event("chat", "user_message", text)
    llm.fail = True
    report = await ing.ingest_pending()
    assert report.deferred == 1 and len(store.pending_events()) == 1 and store.list_memories() == []
    llm.fail = False
    report = await ing.ingest_pending()
    assert report.added == 1 and store.pending_events() == []
    vault.close()


async def test_malformed_candidates_are_dropped_not_fatal(tmp_path, key):  # noqa: F811
    text = "two things happened today"
    table = {
        text: [
            {"content": "bad type", "memory_type": "gossip", "origin": "user_stated"},
            cand("Good one"),
        ]
    }
    vault, store, _llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", text)
    report = await ing.ingest_pending()
    assert report.candidates == 1 and [m.content for m in store.list_memories()] == ["Good one"]
    vault.close()


async def test_secrets_never_reach_the_model(tmp_path, key):  # noqa: F811
    vault, store, llm, ing = setup(tmp_path, key, {})
    store.record_event("chat", "user_message", "my password is hunter22, remember to rotate it")
    await ing.ingest_pending()
    assert llm.seen_prompts and all("hunter22" not in p for p in llm.seen_prompts)
    vault.close()


async def test_unrelated_candidate_is_added_without_a_decision_call(tmp_path, key):  # noqa: F811
    t1, t2 = "I like green tea", "The dentist is on Monday"
    table = {t1: [cand("Likes green tea")], t2: [cand("Dentist visit on Monday", "episodic")]}
    vault, store, llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", t1)
    store.record_event("chat", "user_message", t2)
    report = await ing.ingest_pending()
    assert report.added == 2 and llm.decide_calls == 0
    vault.close()


async def test_invalid_decisions_fall_back_to_add(tmp_path, key):  # noqa: F811
    t1, t2 = "I prefer meetings after 14:00", "I prefer meetings after 15:00"
    table = {
        t1: [cand("Prefers meetings after 14:00")],
        t2: [cand("Prefers meetings after 15:00 now")],
    }
    vault, store, llm, ing = setup(tmp_path, key, table)
    llm.force_decision = {"action": "SUPERSEDE", "target_id": 9999}  # not one of the neighbours
    store.record_event("chat", "user_message", t1)
    await ing.ingest_pending()
    store.record_event("chat", "user_message", t2)
    report = await ing.ingest_pending()
    assert report.invalid_decisions == 1 and report.added == 1
    assert len(store.list_memories()) == 2  # nothing was superseded or lost
    vault.close()


async def test_inferred_never_overwrites_a_stated_fact(tmp_path, key):  # noqa: F811
    t1, t2 = "I prefer meetings after 14:00", "calendar shows only morning meetings"
    table = {
        t1: [cand("Prefers meetings after 14:00")],
        t2: [cand("Prefers morning meetings", origin="inferred")],
    }
    vault, store, llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", t1)
    await ing.ingest_pending()
    store.record_event("calendar", "tool_result", t2)
    llm.force_decision = {"action": "SUPERSEDE", "target_id": 1}
    report = await ing.ingest_pending()
    assert report.conflicts == 1 and report.superseded == 0
    assert store.get(1).status == "active"  # the stated fact survives
    assert vault.query("SELECT from_id, to_id, relation FROM memory_links") == [
        (2, 1, "contradicts")
    ]
    vault.close()


async def test_decision_model_failure_keeps_both(tmp_path, key):  # noqa: F811
    t1, t2 = "I prefer meetings after 14:00", "I prefer meetings after 15:00"
    table = {
        t1: [cand("Prefers meetings after 14:00")],
        t2: [cand("Prefers meetings after 15:00 now")],
    }
    vault, store, llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", t1)
    await ing.ingest_pending()

    async def broken(messages, schema):
        if schema is DECIDE_SCHEMA:
            raise GatewayError("down")
        return await ScriptedLlm.json(llm, messages, schema)

    ing.llm.json = broken
    store.record_event("chat", "user_message", t2)
    await ing.ingest_pending()
    assert len(store.list_memories()) == 2
    vault.close()


async def test_explicit_remember_adds_updates_and_supersedes(tmp_path, key):  # noqa: F811
    vault, store, _llm, ing = setup(tmp_path, key, {})
    action, first = await ing.remember_explicit("Prefers meetings after 14:00", "preference")
    assert action == "ADD"
    action, same = await ing.remember_explicit("prefers  MEETINGS after 14:00", "preference")
    assert action == "NOOP" and same == first  # near-duplicate, no model call
    action, newer = await ing.remember_explicit("Prefers meetings after 15:00 now", "preference")
    assert action == "SUPERSEDE" and store.get(first).status == "superseded"
    assert store.get(newer).origin == "user_stated" and store.get(newer).importance == 0.8
    vault.close()


async def test_fourteen_day_timeline(tmp_path, key):  # noqa: F811
    """Facts change over two weeks; on day 15 we get the current fact and keep the history."""
    clock = Clock()
    table = {
        "I prefer meetings after 14:00": [cand("Prefers meetings after 14:00")],
        "My manager is Anil": [
            cand("Manager is Anil", "semantic", entities=[{"name": "Anil", "kind": "person"}])
        ],
        "Actually make it after 15:00 now": [cand("Prefers meetings after 15:00 now")],
        "Started on the billing project": [cand("Working on the billing project", "project")],
        "Still prefer meetings after 15:00": [cand("Prefers meetings after 15:00")],
        "Sunita is my manager now": [
            cand(
                "Manager is now Sunita", "semantic", entities=[{"name": "Sunita", "kind": "person"}]
            )
        ],
        "ok thanks": [],
    }
    vault, store, _llm, ing = setup(tmp_path, key, table, clock)
    svc = MemoryService(store, FakeEmbedder())
    timeline = [
        (0, "I prefer meetings after 14:00"),
        (0, "My manager is Anil"),
        (3, "Actually make it after 15:00 now"),
        (5, "Started on the billing project"),
        (8, "Still prefer meetings after 15:00"),
        (11, "Sunita is my manager now"),
        (13, "ok thanks"),
    ]
    day = 0
    for d, text in timeline:
        clock.advance(days=d - day)
        day = d
        store.record_event("chat", "user_message", text)
        await ing.ingest_pending()

    clock.advance(days=1)  # day 14 -> ask
    meetings = await svc.recall("when do I like meetings")
    assert [h.memory.content for h in meetings] == ["Prefers meetings after 15:00 now"]
    manager = await svc.recall("who is my manager")
    assert manager[0].memory.content == "Manager is now Sunita"
    assert "Anil" not in " ".join(h.memory.content for h in manager)

    current = meetings[0].memory
    chain = store.history(current.id)
    assert [m.content for m in chain] == [
        "Prefers meetings after 15:00 now",
        "Prefers meetings after 14:00",
    ]
    assert chain[1].status == "superseded" and chain[1].valid_to == "2026-10-04T09:00:00Z"
    assert current.confidence == pytest.approx(0.97)  # restated on day 8: reinforced once
    prov = vault.query("SELECT count(*) FROM memory_sources WHERE memory_id = ?", (current.id,))
    assert prov == [(2,)]  # the day-3 change and the day-8 restatement
    assert sorted(m.content for m in store.list_memories()) == [
        "Manager is now Sunita",
        "Prefers meetings after 15:00 now",
        "Working on the billing project",
    ]
    assert len(store.index) == 3  # superseded facts are out of the index
    vault.close()


async def test_update_that_carries_new_information_is_kept_as_a_new_version(tmp_path, key):  # noqa: F811
    t1, t2, t3 = (
        "I prefer meetings after 14:00",
        "later please, after 15:00",
        "yes after 15:00 it is",
    )
    table = {
        t1: [cand("Prefers meetings after 14:00")],
        t2: [cand("Prefers meetings after 15:00 now")],
        t3: [cand("Prefers meetings after 15:00")],
    }
    vault, store, llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", t1)
    await ing.ingest_pending()
    llm.force_decision = {"action": "UPDATE", "target_id": 1}  # the model mislabels a change
    store.record_event("chat", "user_message", t2)
    report = await ing.ingest_pending()
    assert report.superseded == 1 and report.updated == 0
    assert store.get(1).status == "superseded"
    assert store.get(2).content == "Prefers meetings after 15:00 now"  # the new value is not lost
    # a pure restatement with the same words is still just reinforcement
    llm.force_decision = {"action": "UPDATE", "target_id": 2}
    store.record_event("chat", "user_message", t3)
    report = await ing.ingest_pending()
    assert report.updated == 1 and report.superseded == 0 and len(store.list_memories()) == 1
    vault.close()


def test_roll_past_dates():
    from datetime import date

    from ragnarok.memory.ingest import roll_past_dates

    today = date(2026, 10, 25)
    roll = roll_past_dates
    assert roll("Anniversary is on 2026-02-14", "my anniversary is 14 February", today) == (
        "Anniversary is on 2027-02-14"
    )
    assert roll("Flight on 2026-10-26", "tomorrow", today) == "Flight on 2026-10-26"  # future
    assert roll("Flight on 2026-10-25", "today", today) == "Flight on 2026-10-25"  # today is fine
    assert roll("Visa on 2026-03-01", "visa interview 1 March 2026", today) == "Visa on 2026-03-01"
    assert roll("Date 2026-13-45", "x", today) == "Date 2026-13-45"  # not a real date
    assert roll("Leap day 2024-02-29", "x", today) == "Leap day 2024-02-29"  # cannot roll safely


async def test_plans_roll_forward_but_past_events_keep_their_date(tmp_path, key):  # noqa: F811
    t1, t2 = "My anniversary is on 14 February", "I went to Goa on 5 March"
    table = {
        t1: [cand("Anniversary is on 2026-02-14", "episodic")],  # the model says episodic
        t2: [cand("Went to Goa on 2026-03-05", "episodic")],
    }
    vault, store, _llm, ing = setup(tmp_path, key, table)
    store.record_event("chat", "user_message", t1)
    store.record_event("chat", "user_message", t2)
    await ing.ingest_pending()
    got = sorted(m.content for m in store.list_memories())
    assert got == ["Anniversary is on 2027-02-14", "Went to Goa on 2026-03-05"]
    vault.close()
