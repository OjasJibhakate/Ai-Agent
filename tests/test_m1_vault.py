"""M1: the vault opens, migrates, encrypts at rest, and keeps FTS5 in step with memories."""

import os

import pytest
import sqlcipher3

from ragnarok.memory import vault as vault_mod
from ragnarok.memory.vault import Vault, VaultKeyError, VaultVersionError

NOW = "2026-10-25T12:00:00Z"
SECRET = "SECRET-DEMO-STRING-4711"


@pytest.fixture()
def key() -> bytes:
    return os.urandom(32)


@pytest.fixture()
def vault(tmp_path, key):
    v = Vault.open(tmp_path / "vault.db", key)
    yield v
    v.close()


def add_memory(v: Vault, content: str, **over) -> int:
    row = {
        "content": content,
        "memory_type": "preference",
        "origin": "user_stated",
        "confidence": 0.95,
        "source": "chat",
        "created_at": NOW,
        "content_hash": f"h:{content}",
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with v.transaction() as con:
        return con.execute(
            f"INSERT INTO memories({cols}) VALUES ({marks})", tuple(row.values())
        ).lastrowid


def fts_ids(v: Vault, query: str) -> list[int]:
    return [
        r[0] for r in v.query("SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?", (query,))
    ]


def test_create_close_reopen_persists(tmp_path, key):
    path = tmp_path / "vault.db"
    with Vault.open(path, key) as v:
        mid = add_memory(v, "Prefers meetings after 14:00")
    with Vault.open(path, key) as v:
        assert v.query("SELECT content FROM memories WHERE id = ?", (mid,)) == [
            ("Prefers meetings after 14:00",)
        ]


def test_pragmas_applied(vault):
    assert vault.query("PRAGMA journal_mode")[0][0] == "wal"
    assert vault.query("PRAGMA foreign_keys")[0][0] == 1
    assert vault.query("PRAGMA secure_delete")[0][0] == 1
    assert vault.query("PRAGMA temp_store")[0][0] == 2  # MEMORY
    assert vault.query("PRAGMA synchronous")[0][0] == 2  # FULL


def test_header_is_not_plain_sqlite_and_secret_absent(tmp_path, key):
    path = tmp_path / "vault.db"
    v = Vault.open(path, key)
    add_memory(v, f"project code word {SECRET}")
    wal = path.with_name(path.name + "-wal")
    # While the vault is open the data may still sit in the WAL; it must be encrypted there too.
    assert SECRET.encode() not in wal.read_bytes()
    v.checkpoint()
    v.close()
    raw = path.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert SECRET.encode() not in raw


def test_missing_or_wrong_key_fails(tmp_path, key):
    path = tmp_path / "vault.db"
    Vault.open(path, key).close()
    with pytest.raises(VaultKeyError):
        Vault.open(path, os.urandom(32))
    plain = sqlcipher3.connect(str(path))
    with pytest.raises(sqlcipher3.DatabaseError):
        plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
    plain.close()


@pytest.mark.parametrize("bad", [b"", b"short", os.urandom(31), os.urandom(33), "a" * 32])
def test_key_must_be_32_bytes(tmp_path, bad):
    with pytest.raises(VaultKeyError):
        Vault.open(tmp_path / "vault.db", bad)  # type: ignore[arg-type]


def test_migrations_apply_once_and_set_version(tmp_path, key):
    path = tmp_path / "vault.db"
    with Vault.open(path, key) as v:
        assert v.schema_version == len(vault_mod._load_migrations())
        add_memory(v, "kept across reopen")
    with Vault.open(path, key) as v:  # re-running migrations would fail on CREATE TABLE
        assert v.query("SELECT count(*) FROM memories") == [(1,)]


def test_newer_schema_is_refused(tmp_path, key):
    path = tmp_path / "vault.db"
    with Vault.open(path, key) as v:
        v.execute("PRAGMA user_version = 999")
    with pytest.raises(VaultVersionError):
        Vault.open(path, key)


def test_failed_migration_rolls_back(tmp_path, key, monkeypatch):
    bad = [(1, "CREATE TABLE t(x); THIS IS NOT SQL;")]
    monkeypatch.setattr(vault_mod, "_load_migrations", lambda: bad)
    with pytest.raises(sqlcipher3.Error):
        Vault.open(tmp_path / "vault.db", key)
    monkeypatch.undo()
    with Vault.open(tmp_path / "vault.db", key) as v:  # clean state: full schema applies
        assert v.query("SELECT count(*) FROM memories") == [(0,)]


def test_transaction_rolls_back_on_error(vault):
    with pytest.raises(RuntimeError), vault.transaction() as con:
        con.execute("INSERT INTO projects(name) VALUES ('p')")
        raise RuntimeError("boom")
    assert vault.query("SELECT count(*) FROM projects") == [(0,)]


def test_fts_tracks_insert_update_delete(vault):
    mid = add_memory(vault, "test_refund_rounding failing on port 8089")
    assert fts_ids(vault, '"8089"') == [mid]
    assert fts_ids(vault, '"test_refund_rounding"') == [mid]
    with vault.transaction() as con:
        con.execute("UPDATE memories SET content = 'now passes on port 9090' WHERE id = ?", (mid,))
    assert fts_ids(vault, '"8089"') == []
    assert fts_ids(vault, '"9090"') == [mid]
    with vault.transaction() as con:
        con.execute("DELETE FROM memories WHERE id = ?", (mid,))
    assert fts_ids(vault, '"9090"') == []


def test_check_constraints_reject_bad_values(vault):
    with pytest.raises(sqlcipher3.IntegrityError):
        add_memory(vault, "x", memory_type="gossip")
    with pytest.raises(sqlcipher3.IntegrityError):
        add_memory(vault, "y", origin="guessed")
    with pytest.raises(sqlcipher3.IntegrityError):
        add_memory(vault, "z", confidence=1.5)


def test_hard_delete_cascades(vault):
    mid = add_memory(vault, "dentist appointment on 3 Nov")
    other = add_memory(vault, "related note")
    with vault.transaction() as con:
        ev = con.execute(
            "INSERT INTO events(ts, source, kind, payload, content_hash) VALUES (?,?,?,?,?)",
            (NOW, "chat", "user_message", "dentist 3 Nov", "e1"),
        ).lastrowid
        con.execute("INSERT INTO memory_sources VALUES (?, ?)", (mid, ev))
        con.execute("INSERT INTO memory_links VALUES (?, ?, 'related')", (mid, other))
        con.execute("INSERT INTO memory_tags VALUES (?, 'health')", (mid,))
        con.execute("INSERT INTO entities(name, kind) VALUES ('dentist', 'person')")
        con.execute("INSERT INTO memory_entities VALUES (?, 1)", (mid,))
        con.execute(
            "INSERT INTO embeddings VALUES (?, 'nomic-embed-text', 4, ?)", (mid, b"\0" * 16)
        )
    with vault.transaction() as con:
        con.execute("DELETE FROM memories WHERE id = ?", (mid,))
    for table, col in (
        ("memory_sources", "memory_id"),
        ("memory_links", "from_id"),
        ("memory_tags", "memory_id"),
        ("memory_entities", "memory_id"),
        ("embeddings", "memory_id"),
    ):
        assert vault.query(f"SELECT count(*) FROM {table} WHERE {col} = ?", (mid,)) == [(0,)]
    assert fts_ids(vault, "dentist") == []
    assert vault.query("SELECT count(*) FROM events") == [(1,)]  # the raw event is separate
    assert vault.query("SELECT count(*) FROM memories WHERE id = ?", (other,)) == [(1,)]
