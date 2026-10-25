"""The terminal front end: init, unlock, memory commands, doctor, and a scripted chat session."""

import getpass

import pytest

from ragnarok import cli
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.vault import keys
from tests.fake_embedder import FakeEmbedder
from tests.test_m4_agent import ScriptedChat, call, say

PASSPHRASE = "correct horse battery"
FAST_KDF = keys.KdfParams(time_cost=1, memory_kib=64, parallelism=1)


class FakeGateway:
    """Stands in for the Ollama gateway so the CLI can run without a model."""

    def __init__(self, *args, chat_replies=(), **kwargs):
        self.emb = FakeEmbedder()
        self.scripted = ScriptedChat(chat_replies)
        self.json_calls = 0

    async def embed(self, texts, model=None):
        return await self.emb.embed(texts)

    async def chat(self, messages, *, tools=None, on_delta=None):
        return await self.scripted.chat(messages, tools=tools, on_delta=on_delta)

    async def chat_json(self, messages, schema, **kwargs):
        self.json_calls += 1
        return {"memories": []}

    async def warm_up(self):
        return 0.0

    async def aclose(self):
        pass


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "rag"))
    monkeypatch.setattr(keys, "DEFAULT_KDF", FAST_KDF)
    # init uses create_keystore's default argument; point it at the cheap parameters
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


def test_init_creates_an_encrypted_vault_and_shows_the_recovery_code_once(
    home, monkeypatch, capsys
):
    out = init_vault(monkeypatch, capsys)
    code = next(part for part in out.split() if part.count("-") == 7)
    assert len(code.replace("-", "")) == 32
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


def test_wrong_passphrase_is_a_clean_error(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    passphrases(monkeypatch, "not the passphrase")
    assert cli.main(["memory", "list"]) == 2
    assert "wrong passphrase" in capsys.readouterr().err


def test_recovery_code_unlocks(home, monkeypatch, capsys):
    out = init_vault(monkeypatch, capsys)
    code = next(part for part in out.split() if part.count("-") == 7)
    monkeypatch.setattr(cli, "Gateway", FakeGateway)
    passphrases(monkeypatch, code)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")
    assert cli.main(["chat", "--recovery"]) == 0


def seed(home, monkeypatch, texts):
    key = keys.unlock_with_passphrase(home / "key.json", PASSPHRASE)
    vault = Vault.open(home / "vault.db", key)
    store = MemoryStore(vault)
    store.start()
    emb = FakeEmbedder()
    ids = []
    for t in texts:
        import asyncio

        vec = asyncio.run(emb.embed([t]))[0]
        ids.append(store.add_memory(t, vec, memory_type="semantic", source="test"))
    vault.close()
    return ids


def test_memory_commands(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    monkeypatch.setattr(cli, "Gateway", FakeGateway)
    a, b = seed(home, monkeypatch, ["Dentist is Dr Rao", "Prefers meetings after 14:00"])

    def run(*argv):
        passphrases(monkeypatch, PASSPHRASE)
        code = cli.main(["memory", *argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    code, out, _ = run("list")
    assert code == 0 and "[m:1] Dentist is Dr Rao" in out and "[m:2] Prefers meetings" in out
    code, out, _ = run("search", "dentist")
    assert code == 0 and "Dr Rao" in out and "Prefers meetings" not in out
    code, out, _ = run("show", str(a))
    assert "semantic, user_stated 0.95" in out
    assert run("archive", str(a))[0] == 0 and "Dr Rao" not in run("list")[1]
    assert "Dr Rao" in run("list", "--status", "archived")[1]
    assert run("restore", str(a))[0] == 0 and "Dr Rao" in run("list")[1]
    code, out, _ = run("forget", str(b))
    assert "deleted" in out and "Prefers meetings" not in run("list")[1]
    code, _, err = run("show", "abc")
    assert code == 2 and "needs a memory number" in err
    code, _, err = run("restore", str(a))
    assert code == 2 and "not archived" in err


def test_chat_session_saves_and_remembers(home, monkeypatch, capsys):
    init_vault(monkeypatch, capsys)
    replies = [
        call("memory_save", content="Likes green tea", memory_type="preference"),
        say("Noted, green tea."),
    ]
    gateway = FakeGateway(chat_replies=replies)
    monkeypatch.setattr(cli, "Gateway", lambda *a, **k: gateway)
    typed = iter(["Remember that I like green tea", "/memories", "/profile", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    passphrases(monkeypatch, PASSPHRASE)
    assert cli.main(["chat"]) == 0
    out = capsys.readouterr().out
    assert "rag> Noted, green tea." in out
    assert "[memory_save ok] Saved [m:1]" in out
    assert "[m:1] Likes green tea" in out  # /memories
    assert "- Likes green tea" in out  # /profile
    assert gateway.json_calls == 1  # the session was mined for memories on the way out

    passphrases(monkeypatch, PASSPHRASE)  # a later run still has it
    assert cli.main(["memory", "list"]) == 0
    assert "Likes green tea" in capsys.readouterr().out


def test_doctor_reports_unreachable_ollama(home, monkeypatch, capsys):
    monkeypatch.setenv("RAGNAROK_OLLAMA_URL", "http://127.0.0.1:9")
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out
