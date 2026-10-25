"""M5: consolidation, forgetting, the delete cascade, and reversibility."""

import os
import re
from datetime import UTC, datetime, timedelta

import pytest

from ragnarok.memory.consolidate import SUMMARY_SCHEMA, novel_terms, validate
from ragnarok.memory.forget import usefulness
from ragnarok.persistent_memory import PersistentMemory
from ragnarok.runtime.gateway import GatewayError
from tests.fake_embedder import FakeEmbedder, embed_text
from tests.test_m2_memory import Clock
from tests.test_m3_ingest import ScriptedLlm

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)  # a Tuesday: ISO week 36


@pytest.fixture()
def key() -> bytes:
    return os.urandom(32)


class SummaryLlm(ScriptedLlm):
    """Answers consolidation prompts with whatever `reply(ids, contents)` returns."""

    def __init__(self, reply=None):
        super().__init__({})
        self.reply = reply or self.default
        self.summary_calls = 0

    @staticmethod
    def default(ids, contents):
        return {"summary": "Worked with Priya on the budget.", "cited_ids": ids[:3]}

    async def json(self, messages, schema):
        if schema is SUMMARY_SCHEMA:
            self.summary_calls += 1
            if self.fail:
                raise GatewayError("model down")
            found = re.findall(r"\[m:(\d+) [^\]]+\] (.*)", messages[-1]["content"])
            ids = [int(i) for i, _ in found]
            return self.reply(ids, [c for _, c in found])
        return await super().json(messages, schema)


def open_pm(tmp_path, key, llm=None, clock=None):
    clock = clock or Clock(T0)
    pm = PersistentMemory.open(
        tmp_path / "vault.db", key, embedder=FakeEmbedder(), llm=llm or SummaryLlm()
    )
    pm.store.clock = clock
    pm.store.min_semantic = 0.1
    return pm, clock


def add(pm, clock, text, *, days_ago=0.0, mtype="episodic", **kw):
    """Insert a memory dated `days_ago` before the clock's present, bypassing the decision step."""
    present = clock.now
    clock.now = present - timedelta(days=days_ago)
    try:
        return pm.store.add_memory(text, embed_text(text), memory_type=mtype, source="test", **kw)
    finally:
        clock.now = present


BUDGET_WEEK = [
    "Met Priya about the budget",
    "Priya sent the budget draft",
    "Reviewed the budget with Priya",
    "Budget review moved to Friday",
]


def week_of_notes(pm, clock, texts=BUDGET_WEEK, days_ago=40, **kw):
    return [add(pm, clock, t, days_ago=days_ago - i * 0.1, **kw) for i, t in enumerate(texts)]


# ---- forgetting ------------------------------------------------------------------------------


async def test_usefulness_follows_the_documented_formula(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    fresh = pm.store.get(add(pm, clock, "A fresh note"))
    now = clock.now
    # 0.35*importance(0.5) + 0.25*recency(1.0) + 0.25*use(0) + 0.15*confidence(0.95)
    assert usefulness(fresh, now, 0) == pytest.approx(0.175 + 0.25 + 0 + 0.1425)
    old = pm.store.get(add(pm, clock, "An old note", days_ago=60))
    assert usefulness(old, now, 0) == pytest.approx(0.175 + 0.25 * 0.25 + 0.1425)  # two half-lives
    used = old.model_copy(update={"access_count": 10, "last_accessed_at": now.isoformat()})
    assert usefulness(used, now, 10) == pytest.approx(0.175 + 0.25 + 0.25 + 0.1425)
    await pm.close()


async def test_prune_only_archives_covered_unimportant_unpinned_memories(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(pm, clock, days_ago=100)  # 100 days old: recency has faded
    pinned = add(pm, clock, "Pinned old note", days_ago=100, pinned=True)
    uncovered = add(pm, clock, "Old but never summarised", days_ago=100)
    important = add(pm, clock, "Old but important", days_ago=100, importance=1.0)
    sid = pm.store.add_memory(
        "Worked with Priya on the budget.", embed_text("budget"), memory_type="summary",
        source="consolidation", origin="inferred",
    )  # fmt: skip
    for i in [*ids, pinned, important]:
        pm.store.link(sid, i, "derived_from")

    dry = await pm.prune(dry_run=True)
    assert sorted(dry.archived) == sorted(ids) and all(
        pm.store.get(i).status == "active" for i in ids
    )  # a dry run changes nothing
    report = await pm.prune()
    assert sorted(report.archived) == sorted(ids)
    assert all(pm.store.get(i).status == "archived" for i in ids)
    assert pm.store.get(pinned).status == "active"  # pinned: never
    assert pm.store.get(important).status == "active"  # useful enough
    assert pm.store.get(uncovered).status == "active"  # not covered by a summary: never
    assert pm.store.get(sid).status == "active"  # summaries are not pruned
    again = await pm.prune()
    assert again.archived == []  # safe to repeat
    assert len(pm.store.index) == 4  # archived ones left the index
    await pm.close()


async def test_a_memory_that_keeps_being_used_is_not_forgotten(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(pm, clock, days_ago=100)
    sid = pm.store.add_memory("A summary.", embed_text("summary"), memory_type="summary",
                              source="consolidation", origin="inferred")  # fmt: skip
    for i in ids:
        pm.store.link(sid, i, "derived_from")
    for _ in range(30):  # recalled over and over, recently
        pm.store.touch([ids[0]])
    assert sorted((await pm.prune()).archived) == sorted(ids[1:])
    assert pm.store.get(ids[0]).status == "active"
    await pm.close()


# ---- consolidation: planning -------------------------------------------------------------------


async def test_groups_are_per_week_and_project_and_need_enough_notes(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    week_of_notes(pm, clock, days_ago=40)  # a full group
    week_of_notes(
        pm,
        clock,
        ["Alpha one", "Alpha two", "Alpha three", "Alpha four"],
        days_ago=40,
        project="alpha",
    )
    add(pm, clock, "Lonely note", days_ago=20)  # a week with a single note
    add(pm, clock, "Fresh note one", days_ago=1)  # this week is still filling up
    week_of_notes(pm, clock, ["Fresh a", "Fresh b", "Fresh c", "Fresh d"], days_ago=2)
    from ragnarok.memory.consolidate import Consolidator

    groups, small = Consolidator(pm.store, pm.service.embedder, SummaryLlm()).plan()
    assert len(groups) == 2 and small == 1
    assert {g.key[0] is None for g in groups} == {True, False}  # one with a project, one without
    assert all(len(g.members) == 4 for g in groups)
    await pm.close()


async def test_only_episodic_unpinned_uncovered_notes_are_grouped(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    week_of_notes(pm, clock, mtype="semantic")  # facts, not episodes
    week_of_notes(pm, clock, ["P one", "P two", "P three", "P four"], pinned=True)
    report = await pm.consolidate()
    assert report.groups == 0 and report.summarized == 0
    await pm.close()


# ---- consolidation: what is stored ---------------------------------------------------------------


async def test_a_valid_summary_is_stored_with_provenance_and_nothing_else_changes(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(pm, clock, entities=[("Priya", "person")], importance=0.4)
    ids[0] and pm.store.touch([ids[0]])
    before = {i: pm.store.get(i).content for i in ids}
    report = await pm.consolidate()
    assert (report.groups, report.summarized, report.errors) == (1, 1, 0)
    sid = report.summary_ids[0]
    summary = pm.store.get(sid)
    assert summary.memory_type == "summary" and summary.origin == "inferred"
    assert summary.confidence <= 0.7 and summary.confidence <= min(
        pm.store.get(i).confidence for i in ids[:3]
    )
    assert summary.importance == 0.4 and summary.status == "active"
    assert pm.store.sources_of(sid) == sorted(ids[:3])  # the ids the model cited
    assert pm.store.entities_of_memory(sid) == [("Priya", "person")]
    for i in ids:  # sources untouched
        assert pm.store.get(i).status == "active" and pm.store.get(i).content == before[i]
    assert next(h.memory.id for h in await pm.recall("Priya budget summary")) in {sid, *ids}
    assert len(pm.store.index) == 5  # four notes plus the summary
    await pm.close()


async def test_a_second_run_does_not_summarise_what_is_already_covered(tmp_path, key):
    llm = SummaryLlm()
    pm, clock = open_pm(tmp_path, key, llm)
    week_of_notes(pm, clock)
    first = await pm.consolidate()
    second = await pm.consolidate()
    assert first.summarized == 1 and second.summarized == 0
    assert llm.summary_calls == 1  # the 4th note (uncited) is a group of one: too small
    assert second.groups == 0 and second.too_small == 1
    assert pm.store.count() == 5
    await pm.close()


async def test_limit_caps_work_per_call_and_the_rest_waits_for_the_next(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    for n, days in enumerate((40, 50, 60)):
        week_of_notes(
            pm, clock, [f"Topic{n} a", f"Topic{n} b", f"Topic{n} c", f"Topic{n} d"], days_ago=days
        )
    llm = pm.ingestor.llm
    llm.reply = lambda ids, contents: {"summary": "A week of topics.", "cited_ids": ids}
    one = await pm.consolidate(limit=1)
    assert one.groups == 3 and one.summarized == 1
    rest = await pm.consolidate(limit=5)
    assert rest.summarized == 2
    await pm.close()


# ---- consolidation: what is rejected -----------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (
            lambda ids, c: {
                "summary": "Worked with Priya on the budget.",
                "cited_ids": [ids[0], 9999],
            },
            "unknown_id",
        ),
        (
            lambda ids, c: {"summary": "Worked with Priya on the budget.", "cited_ids": [ids[0]]},
            "too_few_sources",
        ),
        (
            lambda ids, c: {
                "summary": "Worked with Priya on the budget.",
                "cited_ids": [ids[0], ids[0]],
            },
            "too_few_sources",
        ),
        (
            lambda ids, c: {
                "summary": "Worked with Priya and Rahul on the budget.",
                "cited_ids": ids,
            },
            "new_detail",
        ),
        (
            lambda ids, c: {"summary": "Priya approved a 40 percent budget cut.", "cited_ids": ids},
            "new_detail",
        ),
        (lambda ids, c: {"summary": "x" * 500, "cited_ids": ids}, "length"),
        (lambda ids, c: {"summary": "   ", "cited_ids": ids}, "length"),
    ],
)
async def test_bad_summaries_are_rejected_and_nothing_is_stored(tmp_path, key, reply, reason):
    pm, clock = open_pm(tmp_path, key, SummaryLlm(reply))
    week_of_notes(pm, clock)
    report = await pm.consolidate()
    assert report.rejected == {reason: 1} and report.summarized == 0
    assert pm.store.count() == 4  # no summary, and no source touched
    await pm.close()


async def test_model_failures_store_nothing_and_the_next_run_retries(tmp_path, key):
    llm = SummaryLlm()
    pm, clock = open_pm(tmp_path, key, llm)
    week_of_notes(pm, clock)
    llm.fail = True
    down = await pm.consolidate()
    assert down.errors == 1 and down.summarized == 0 and pm.store.count() == 4
    llm.fail = False
    assert (await pm.consolidate()).summarized == 1  # nothing was marked done, so it retries
    llm.reply = lambda ids, contents: {"summary": "ok"}  # malformed: no cited_ids
    for t in ("Other one", "Other two", "Other three", "Other four"):
        add(pm, clock, t, days_ago=70)
    assert (await pm.consolidate()).errors == 1
    await pm.close()


def test_a_hyphenated_form_of_words_in_the_sources_is_not_invented():
    source = "Started the 30 day flossing habit"
    assert novel_terms("Started a 30-day flossing habit.", source) == []
    assert novel_terms("Started a 60-day flossing habit.", source) == ["60-day"]
    assert novel_terms("Started a 30-week habit.", source) == [
        "30-week"
    ]  # 'week' is not in the source


def test_alphanumeric_words_are_one_word_on_both_sides():
    source = "Exported the mp3 files for sprint w3 and the B2B demo"
    assert novel_terms("Exported mp3 files for sprint w3 and the B2B demo.", source) == []
    assert novel_terms("Exported mp4 files.", source) == ["mp4"]


def test_novel_terms_catches_invented_names_and_numbers_only():
    source = "Met Priya about the budget on Friday. Priya sent 3 drafts."
    assert novel_terms("Worked with Priya on the budget.", source) == []
    assert (
        novel_terms("Priya's drafts were sent on Friday.", source) == []
    )  # possessive, same words
    assert novel_terms("The budget went to Rahul.", source) == ["Rahul"]
    assert novel_terms("Priya sent 5 drafts.", source) == ["5"]
    assert (
        novel_terms("Priya sent 3 drafts. Then it ended.", source) == []
    )  # sentence-start words ok
    assert validate("Priya sent 3 drafts.", [1, 2], {1, 2}, source) is None


# ---- deleting a fact deletes what was built from it ----------------------------------------------


async def test_deleting_a_source_removes_the_summary_and_every_trace(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(
        pm,
        clock,
        [
            "Visited Dr Zorblatt about the knee",
            "Knee scan booked",
            "Knee brace ordered",
            "Knee review",
        ],
    )
    pm.ingestor.llm.reply = lambda i, c: {
        "summary": "Visited Dr Zorblatt about the knee.",
        "cited_ids": i,
    }
    sid = (await pm.consolidate()).summary_ids[0]
    assert pm.store.get(sid) is not None and len(pm.store.index) == 5

    assert await pm.forget(ids[0]) is True
    assert pm.store.get(ids[0]) is None and pm.store.get(sid) is None  # the summary went too
    assert await pm.recall("Zorblatt") == []
    assert pm.vault.query(
        "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'zorblatt'"
    ) == [(0,)]
    assert pm.vault.query(
        "SELECT count(*) FROM embeddings WHERE memory_id IN (?, ?)", (ids[0], sid)
    ) == [(0,)]
    assert pm.vault.query("SELECT count(*) FROM memory_links") == [(0,)]
    assert len(pm.store.index) == 3  # the other three notes only
    tombstones = pm.vault.query("SELECT action, ref_id FROM audit_log ORDER BY ref_id")
    assert tombstones == [("memory_deleted", ids[0]), ("memory_deleted", sid)]  # ids only, no text
    assert all(pm.store.get(i).status == "active" for i in ids[1:])  # the rest are untouched
    await pm.close()


async def test_deleting_a_summary_leaves_its_sources_alone(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(pm, clock)
    sid = (await pm.consolidate()).summary_ids[0]
    await pm.forget(sid)
    assert all(pm.store.get(i).status == "active" for i in ids) and pm.store.covered_ids() == set()
    await pm.close()


async def test_undoing_a_summary_restores_what_pruning_had_hidden(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    ids = week_of_notes(pm, clock, days_ago=100)
    sid = (await pm.consolidate()).summary_ids[0]
    await pm.prune()
    assert {pm.store.get(i).status for i in ids[:3]} == {"archived"}
    assert await pm.recall("Priya budget draft", k=10) and all(
        h.memory.id not in ids[:3] for h in await pm.recall("Priya budget draft", k=10)
    )
    restored = await pm.undo_summary(sid)
    assert sorted(restored) == sorted(ids[:3]) and pm.store.get(sid) is None
    assert all(pm.store.get(i).status == "active" for i in ids)
    with pytest.raises(ValueError):
        await pm.undo_summary(ids[0])  # not a summary
    await pm.close()


# ---- maintenance ---------------------------------------------------------------------------------


async def test_maintain_runs_everything_once_a_day(tmp_path, key):
    pm, clock = open_pm(tmp_path, key)
    week_of_notes(pm, clock, days_ago=100)
    ev = pm.store.record_event("chat", "user_message", "an old chat line", retention_days=1)
    assert ev
    clock.advance(days=3)
    first = await pm.maintain()
    assert not first.skipped and first.consolidation.summarized == 1
    assert len(first.pruned.archived) == 3 and first.events_expired == 1
    assert pm.store.meta_get("last_maintenance") is not None
    clock.advance(hours=23)
    assert (await pm.maintain()).skipped  # too soon
    assert not (await pm.maintain(force=True)).skipped
    clock.advance(hours=25)
    assert not (await pm.maintain()).skipped
    await pm.close()


async def test_months_of_notes_shrink_to_summaries_while_facts_stay(tmp_path, key):
    """Six weeks of episodes become summaries; stated preferences and recent notes are untouched."""
    pm, clock = open_pm(tmp_path, key)
    pref = add(pm, clock, "Prefers meetings after 14:00", days_ago=120, mtype="preference")
    # one distinct summary per week (identical text would be merged into a single summary)
    pm.ingestor.llm.reply = lambda ids, contents: {
        "summary": f"{contents[0]} and related notes.",
        "cited_ids": ids,
    }
    for week in range(6):
        week_of_notes(
            pm,
            clock,
            [
                f"Project note w{week} a",
                f"Project note w{week} b",
                f"Project note w{week} c",
                f"Project note w{week} d",
            ],
            days_ago=140 - week * 7,
        )
    recent = add(pm, clock, "Yesterday's note", days_ago=1)
    before = pm.store.count()
    report = await pm.maintain(force=True)
    assert (
        report.consolidation.groups == 6 and report.consolidation.summarized == 5
    )  # limit 5 per run
    await pm.maintain(force=True)
    stats = await pm.stats()
    assert stats["by_type"].get("summary") == 6
    assert stats["archived"] >= 18 and stats["active"] < before
    assert pm.store.get(pref).status == "active" and pm.store.get(recent).status == "active"
    assert [h.memory.id for h in await pm.recall("meetings after 14:00")] == [pref]
    await pm.close()
