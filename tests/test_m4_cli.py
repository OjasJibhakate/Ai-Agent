"""The terminal front end: init, unlock, memory commands, doctor, and a scripted chat session."""

import getpass
import re
import time

import pytest

from ragnarok import chat as chat_mod
from ragnarok import cli
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.vault import keys
from tests.fake_embedder import FakeEmbedder
from tests.test_m4_agent import ScriptedChat, call, say

PASSPHRASE = "correct horse battery"
FAST_KDF = keys.KdfParams(time_cost=1, memory_kib=64, parallelism=1)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class FakeGateway:
    """Stands in for the Ollama gateway so the CLI can run without a model."""

    base_url = "http://127.0.0.1:9"

    def __init__(self, *args, chat_replies=(), **kwargs):
        self.emb = FakeEmbedder()
        self.scripted = ScriptedChat(chat_replies)
        self.json_calls = 0
        self.warmed = 0

    async def embed(self, texts, model=None):
        return await self.emb.embed(texts)

    async def chat(self, messages, *, tools=None, on_delta=None):
        return await self.scripted.chat(messages, tools=tools, on_delta=on_delta)

    async def chat_json(self, messages, schema, **kwargs):
        self.json_calls += 1
        if "summary" in schema.get("properties", {}):  # a consolidation request
            ids = [int(i) for i in re.findall(r"\[m:(\d+) ", messages[-1]["content"])]
            return {"summary": "Worked with Priya on the budget.", "cited_ids": ids[:3]}
        return {"memories": []}

    async def warm_up(self):
        self.warmed += 1
        return 0.0

    async def aclose(self):
        pass


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "rag"))
    original = keys.create_keystore
    monkeypatch.setattr(
        keys,
        "create_keystore",
        lambda path, passphrase, kdf=FAST_KDF: original(path, passphrase, kdf),
    )
    return tmp_path / "rag"


def passphrases(monkeypatch, *answers):
    queue = list(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": queue.pop(0))


def init_vault(monkeypatch, capsys) -> str:
    passphrases(monkeypatch, PASSPHRASE, PASSPHRASE)
    assert cli.main(["init"]) == 0
    return capsys.readouterr().out


def recovery_code(out: str) -> str:
    return next(part for part in out.split() if part.count("-") == 7)


def test_init_creates_an_encrypted_vault_and_shows_the_recovery_code_once(
    home, monkeypatch, capsys
):
    out = init_vault(monkeypatch, capsys)
    code = recovery_code(out)
    assert len(code.replace("-", "")) == 32 and "Recovery code" in out
    assert "Save this code somewhere safe" in out and "uv run python -m ragnarok chat" in out
    assert (home / "key.json").exists() and (home / "vault.db").exists()
    assert not (home / "vault.db").read_bytes().startswith(b"SQLite format 3")
    assert code not in (home / "key.json").read_text()  # only a wrap of the key is stored
    assert cli.main(["init"]) == 2  # never overwrites an existing vault
    assert "already initialised" in capsys.readouterr().err


def test_init_rejects_mismatched_or_short_passphrases(home, monkeypatch, capsys):
    passphrases(monkeypatch, PASSPHRASE, "something else entirely")
    assert cli.main(["init"]) == 2
    passphrases(monkeypatch, "short", "short")
    assert cli.main(["init"]) == 2
    assert not (home / "key.json").exists()


def test_refuses_a_data_folder_inside_onedrive(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "OneDrive" / "Desktop" / "rag"))
    passphrases(monkeypatch, PASSPHRASE, PASSPHRASE)
    assert cli.main(["init"]) == 2
    assert "OneDrive" in capsys.readouterr().err


def test_wrong_passphrase_is_a_clean_error_after_three_tries(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    passphrases(monkeypatch, "nope one", "nope two", "nope three")
    assert cli.main(["memory", "list"]) == 2
    captured = capsys.readouterr()
    assert "2 tries left" in captured.out + captured.err
    assert "1 try left" in captured.out + captured.err
    assert "wrong passphrase" in captured.err


def test_a_typo_is_forgiven(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    monkeypatch.setattr(cli, "Gateway", FakeGateway)
    passphrases(monkeypatch, "typo typo typo", PASSPHRASE)
    assert cli.main(["memory", "list"]) == 0


def test_recovery_code_unlocks(home, monkeypatch, capsys):
    code = recovery_code(init_vault(monkeypatch, capsys))
    monkeypatch.setattr(cli, "Gateway", FakeGateway)
    passphrases(monkeypatch, code)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")
    assert cli.main(["chat", "--recovery"]) == 0


def seed(home, texts):
    import asyncio

    key = keys.unlock_with_passphrase(home / "key.json", PASSPHRASE)
    vault = Vault.open(home / "vault.db", key)
    store = MemoryStore(vault)
    store.start()
    emb = FakeEmbedder()
    ids = []
    for t in texts:
        vec = asyncio.run(emb.embed([t]))[0]
        ids.append(store.add_memory(t, vec, memory_type="semantic", source="test"))
    vault.close()
    return ids


def test_memory_commands(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    monkeypatch.setattr(cli, "Gateway", FakeGateway)
    a, b = seed(home, ["Dentist is Dr Rao", "Prefers meetings after 14:00"])

    def run(*argv):
        passphrases(monkeypatch, PASSPHRASE)
        code = cli.main(["memory", *argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    code, out, _ = run("list")
    assert code == 0 and "Dentist is Dr Rao" in out and "Prefers meetings" in out and "m1" in out
    code, out, _ = run("search", "dentist")
    assert code == 0 and "Dr Rao" in out and "Prefers meetings" not in out
    code, out, _ = run("show", str(a))
    assert "semantic, user_stated 0.95" in out
    assert run("archive", str(a))[0] == 0 and "Dr Rao" not in run("list")[1]
    assert "Dr Rao" in run("list", "--status", "archived")[1]
    assert run("restore", str(a))[0] == 0 and "Dr Rao" in run("list")[1]
    code, out, _ = run("forget", str(b))
    assert "Deleted m2" in out and "Prefers meetings" not in run("list")[1]
    code, _, err = run("show", "abc")
    assert code == 2 and "needs a memory number" in err
    code, _, err = run("restore", str(a))
    assert code == 2 and "not archived" in err


def test_chat_session_end_to_end(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    replies = [
        call("memory_save", content="Likes green tea", memory_type="preference"),
        say("Hello! Nice to meet you."),
    ]
    gateway = FakeGateway(chat_replies=replies)
    monkeypatch.setattr(cli, "Gateway", lambda *a, **k: gateway)
    monkeypatch.setattr(chat_mod, "LEARN_AFTER_SECONDS", 0.02)
    typed = iter(["Remember that I like green tea", "hello", "/memories", "/profile", "/quit"])

    def read(prompt):
        line = next(typed)
        if line == "/memories":
            time.sleep(0.4)  # idle time: the background learner reads the chat now
        return line

    monkeypatch.setattr("builtins.input", read)
    passphrases(monkeypatch, PASSPHRASE)
    assert cli.main(["chat"]) == 0
    out = ANSI.sub("", capsys.readouterr().out)
    assert "Welcome." in out and "Ragnarok" in out  # first run: nothing remembered yet
    assert "Saved: Likes green tea [m:1]" in out  # the action's own sentence
    assert "memory_save" in out  # the footer names the tool
    assert "Nice to meet you" in out
    assert "m1" in out and "preference" in out  # /memories
    assert gateway.json_calls >= 1 and gateway.warmed == 1  # learned while idle; warmed once
    assert "See you soon" in out or "finish learning" in out

    passphrases(monkeypatch, PASSPHRASE)  # a later run still has it
    assert cli.main(["memory", "list"]) == 0
    assert "Likes green tea" in capsys.readouterr().out


def test_doctor_reports_unreachable_ollama(home, monkeypatch, capsys):
    monkeypatch.setenv("RAGNAROK_OLLAMA_URL", "http://127.0.0.1:9")
    assert cli.main(["doctor"]) == 1
    assert "not reachable" in capsys.readouterr().out


# ---- the memory controls: pin, correct, revert, export, stats, tidy-up --------------------------


def run_memory(monkeypatch, capsys, *argv):
    passphrases(monkeypatch, PASSPHRASE)
    code = cli.main(["memory", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def setup_controls(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    monkeypatch.setattr(cli, "Gateway", FakeGateway)


def test_pin_unpin_and_the_profile_marks_it(home, monkeypatch, capsys):
    setup_controls(home, monkeypatch, capsys)
    (mid,) = seed(home, ["Call me Sam"])
    code, out, _ = run_memory(monkeypatch, capsys, "pin", str(mid))
    assert code == 0 and "Pinned m1" in out
    assert "pinned" in run_memory(monkeypatch, capsys, "show", str(mid))[1]
    assert "Unpinned m1" in run_memory(monkeypatch, capsys, "unpin", str(mid))[1]
    code, _, err = run_memory(monkeypatch, capsys, "pin", "x")
    assert code == 2 and "needs a memory number" in err
    code, _, err = run_memory(monkeypatch, capsys, "pin", "99")
    assert code == 2 and "not active" in err


def test_correct_revert_and_history(home, monkeypatch, capsys):
    setup_controls(home, monkeypatch, capsys)
    (old,) = seed(home, ["Gym day is Tuesday"])
    code, _, err = run_memory(monkeypatch, capsys, "correct", str(old))
    assert code == 2 and "needs the new text" in err
    code, out, _ = run_memory(
        monkeypatch, capsys, "correct", str(old), "--text", "Gym day is Thursday"
    )
    assert code == 0 and "Replaced m1 with m2" in out
    hist = run_memory(monkeypatch, capsys, "history", "2")[1]
    assert "Gym day is Thursday" in hist and "Gym day is Tuesday" in hist
    code, out, _ = run_memory(monkeypatch, capsys, "revert", str(old))
    assert code == 0 and "Brought back m1" in out and "m2 was archived" in out
    listing = run_memory(monkeypatch, capsys, "list")[1]
    assert "Gym day is Tuesday" in listing and "Thursday" not in listing
    code, _, err = run_memory(monkeypatch, capsys, "revert", "2")
    assert code == 2 and "replaced (superseded)" in err


def test_export_needs_consent_and_writes_every_version(home, monkeypatch, capsys, tmp_path):
    setup_controls(home, monkeypatch, capsys)
    seed(home, ["Lives in Pune"])
    target = tmp_path / "memories.jsonl"
    code, _, err = run_memory(monkeypatch, capsys, "export", str(target))
    assert code == 2 and "--yes-plaintext" in err and not target.exists()
    code, out, _ = run_memory(monkeypatch, capsys, "export", str(target), "--yes-plaintext")
    assert code == 0 and "not encrypted" in out
    assert "Lives in Pune" in target.read_text(encoding="utf-8")
    assert run_memory(monkeypatch, capsys, "export")[0] == 2  # no path given


def test_stats_and_the_tidy_up_commands(home, monkeypatch, capsys):
    setup_controls(home, monkeypatch, capsys)
    seed(home, ["Prefers tea"])
    code, out, _ = run_memory(monkeypatch, capsys, "stats")
    assert code == 0 and "1" in out and "active" in out and "not yet learned" in out
    code, out, _ = run_memory(monkeypatch, capsys, "consolidate")
    assert code == 0 and "0 of 0 weekly groups summarised" in out
    code, out, _ = run_memory(monkeypatch, capsys, "prune", "--dry-run")
    assert code == 0 and "Would archive 0 of 0" in out
    code, out, _ = run_memory(monkeypatch, capsys, "maintain")
    assert code == 0 and "0 summaries" in out


def test_consolidate_then_undo_summary_through_the_cli(home, monkeypatch, capsys):
    import asyncio
    from datetime import UTC, datetime, timedelta

    setup_controls(home, monkeypatch, capsys)
    key = keys.unlock_with_passphrase(home / "key.json", PASSPHRASE)
    vault = Vault.open(home / "vault.db", key)
    store = MemoryStore(vault)
    store.start()
    notes = ["Met Priya about the budget", "Priya sent the budget draft",
             "Reviewed the budget with Priya", "Budget review moved to Friday"]  # fmt: skip
    emb = FakeEmbedder()
    base = datetime.now(UTC) - timedelta(days=40)
    for i, t in enumerate(notes):
        store.clock = lambda i=i: base + timedelta(hours=i)
        store.add_memory(t, asyncio.run(emb.embed([t]))[0], memory_type="episodic", source="test")
    vault.close()

    code, out, _ = run_memory(monkeypatch, capsys, "consolidate")
    assert code == 0 and "1 of 1 weekly groups summarised" in out
    listing = run_memory(monkeypatch, capsys, "list", "--type", "summary")[1]
    assert "Worked with Priya on the budget." in listing and "summary" in listing
    code, out, _ = run_memory(monkeypatch, capsys, "undo-summary", "5")
    assert code == 0 and "Removed summary m5" in out
    assert (
        "Worked with Priya" not in run_memory(monkeypatch, capsys, "list", "--type", "summary")[1]
    )
    code, _, err = run_memory(monkeypatch, capsys, "undo-summary", "1")
    assert code == 2 and "not a summary" in err
