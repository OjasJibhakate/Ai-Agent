"""Encrypted vault: the one SQLCipher file holding all durable memory (NFR1.2, MEMORY.md section 1).

This module is synchronous. Async code must call it from the DB writer thread or via
`asyncio.to_thread` (AGENTS.md hard rule 6). Key wrapping (passphrase, recovery code, DPAPI) is a
separate layer; the vault only takes the raw 32-byte key and never logs or stores it.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlcipher3

KEY_BYTES = 32
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class VaultError(Exception):
    """Base class for vault failures."""


class VaultKeyError(VaultError):
    """The key is missing, malformed, or does not open this file."""


class VaultVersionError(VaultError):
    """The file was written by a newer schema than this code knows."""


def _load_migrations() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise VaultError(f"bad migration file name: {path.name}")
        found.append((int(match.group(1)), path.read_text(encoding="utf-8")))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise VaultError(f"migrations must be numbered 1..N without gaps, got {versions}")
    return found


class Vault:
    """An open, unlocked vault. Use `Vault.open(path, key)`."""

    def __init__(self, con: sqlcipher3.Connection, path: Path) -> None:
        self._con = con
        self.path = path
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str | Path, key: bytes, *, check: bool = True) -> Vault:
        """Open (or create) the vault at `path`, apply pragmas, and run pending migrations."""
        if not isinstance(key, bytes | bytearray) or len(key) != KEY_BYTES:
            raise VaultKeyError(f"key must be exactly {KEY_BYTES} bytes")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        con = sqlcipher3.connect(str(path), check_same_thread=False, isolation_level=None)
        try:
            con.execute(f"PRAGMA key = \"x'{bytes(key).hex()}'\"")
            try:
                con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except sqlcipher3.DatabaseError as exc:
                raise VaultKeyError("wrong key, or the file is not a vault") from exc
            vault = cls(con, path)
            vault._apply_pragmas()
            if check:
                vault._quick_check()
            vault._migrate()
            return vault
        except BaseException:
            con.close()
            raise

    def _apply_pragmas(self) -> None:
        # WAL pages are encrypted by SQLCipher too. synchronous=FULL so a committed write survives
        # power loss (D-007). temp_store=MEMORY keeps sort spills and temp tables off the disk.
        for pragma in (
            "journal_mode = WAL",
            "synchronous = FULL",
            "temp_store = MEMORY",
            "secure_delete = ON",
            "foreign_keys = ON",
        ):
            self._con.execute(f"PRAGMA {pragma}")

    def _quick_check(self) -> None:
        result = self._con.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise VaultError(f"quick_check failed: {result[0] if result else 'no result'}")

    @property
    def schema_version(self) -> int:
        return self._con.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        migrations = _load_migrations()
        current = self.schema_version
        latest = len(migrations)
        if current > latest:
            raise VaultVersionError(f"vault schema {current} is newer than code schema {latest}")
        for version, sql in migrations:
            if version <= current:
                continue
            with self._lock:
                self._con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")

    @contextmanager
    def transaction(self) -> Iterator[sqlcipher3.Connection]:
        """Run a block atomically: commit on success, roll back on any exception."""
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            else:
                self._con.execute("COMMIT")

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlcipher3.Cursor:
        with self._lock:
            return self._con.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[tuple]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def checkpoint(self) -> None:
        """Fold the WAL into the main file."""
        with self._lock:
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
