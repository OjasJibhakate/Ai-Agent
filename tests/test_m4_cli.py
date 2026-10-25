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
