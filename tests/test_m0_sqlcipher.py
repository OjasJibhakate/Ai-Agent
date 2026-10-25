"""M0: SQLCipher gives us an encrypted file with working FTS5 (NFR1.2 + memory lexical search)."""

import os

import pytest
import sqlcipher3


@pytest.fixture()
def keyed_db(tmp_path):
    path = tmp_path / "vault.db"
    key = os.urandom(32).hex()
    con = sqlcipher3.connect(str(path))
    con.execute(f"PRAGMA key = \"x'{key}'\"")
    yield con, path, key
    con.close()


def test_fts5_is_available(keyed_db):
    con, _, _ = keyed_db
    opts = [r[0] for r in con.execute("PRAGMA compile_options")]
    assert any("FTS5" in o for o in opts)


def test_fts5_exact_identifier_search(keyed_db):
    con, _, _ = keyed_db
    con.execute("create virtual table f using fts5(content)")
    con.execute("insert into f(content) values ('port 8089 auth_middleware.py 0x80070005 error')")
    con.execute("insert into f(content) values ('prefers meetings after 2 pm')")
    con.commit()
    for token in ('"8089"', '"0x80070005"', '"auth_middleware"'):
        assert len(con.execute("select rowid from f where f match ?", (token,)).fetchall()) == 1


def test_file_is_encrypted_at_rest(keyed_db):
    con, path, _ = keyed_db
    con.execute("create table m(content text)")
    con.execute("insert into m values ('SECRET-DEMO-STRING auth_middleware')")
    con.commit()
    con.close()
    raw = path.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert b"SECRET-DEMO-STRING" not in raw


def test_open_without_key_fails(keyed_db):
    con, path, _ = keyed_db
    con.execute("create table m(x)")
    con.commit()
    con.close()
    other = sqlcipher3.connect(str(path))
    with pytest.raises(sqlcipher3.DatabaseError):
        other.execute("select count(*) from sqlite_master").fetchone()
    other.close()


def test_open_with_wrong_key_fails(keyed_db):
    con, path, _ = keyed_db
    con.execute("create table m(x)")
    con.commit()
    con.close()
    other = sqlcipher3.connect(str(path))
    other.execute(f"PRAGMA key = \"x'{os.urandom(32).hex()}'\"")
    with pytest.raises(sqlcipher3.DatabaseError):
        other.execute("select count(*) from sqlite_master").fetchone()
    other.close()
