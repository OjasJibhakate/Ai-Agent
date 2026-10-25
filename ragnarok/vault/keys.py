"""Vault key management (D-006, ARCHITECTURE 6.2).

A random 256-bit vault key (VK) opens the SQLCipher file. It is only ever stored wrapped:
  - by a key derived from the passphrase with Argon2id, and
  - by a key derived from a one-time recovery code with HKDF (the code is high entropy).
An opt-in DPAPI copy (current Windows user) allows unlock at login without typing.

Nothing here logs, prints, or returns secrets other than the key/code a caller asked for.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
VK_BYTES = 32
MIN_PASSPHRASE = 8
DPAPI_ENTROPY = b"ragnarok.vault.v1"
_RECOVERY_BYTES = 20  # 160 bits -> 32 base32 characters


class KeystoreError(Exception):
    """Base class for key-handling failures."""


class WrongSecret(KeystoreError):
    """The passphrase or recovery code does not unlock this keystore."""


@dataclass(frozen=True)
class KdfParams:
    """Argon2id cost: about 0.4 s on the target laptop (measured). Tests lower it."""

    time_cost: int = 6
    memory_kib: int = 256 * 1024
    parallelism: int = 4


@dataclass(frozen=True)
class CreatedKeys:
    vault_key: bytes
    recovery_code: str  # shown to the user once; only a wrap of the key is stored


DEFAULT_KDF = KdfParams()


def runtime_dir() -> Path:
    """%LOCALAPPDATA%\\Ragnarok, outside OneDrive (D-015)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise KeystoreError("LOCALAPPDATA is not set")
    return Path(base) / "Ragnarok"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _passphrase_key(passphrase: str, salt: bytes, kdf: KdfParams) -> bytes:
    secret = unicodedata.normalize("NFKC", passphrase).encode("utf-8")
    return hash_secret_raw(
        secret,
        salt,
        time_cost=kdf.time_cost,
        memory_cost=kdf.memory_kib,
        parallelism=kdf.parallelism,
        hash_len=32,
        type=Type.ID,
    )


def _recovery_key(code: str, salt: bytes) -> bytes:
    cleaned = "".join(code.split()).replace("-", "").upper()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"ragnarok recovery").derive(
        cleaned.encode("ascii", errors="replace")
    )


def _wrap(kek: bytes, vault_key: bytes, label: str) -> dict[str, str]:
    nonce = os.urandom(12)
    ct = AESGCM(kek).encrypt(nonce, vault_key, f"ragnarok/{VERSION}/{label}".encode())
    return {"nonce": _b64(nonce), "ct": _b64(ct)}


def _unwrap(kek: bytes, wrapped: dict[str, str], label: str) -> bytes:
    try:
        return AESGCM(kek).decrypt(
            _unb64(wrapped["nonce"]), _unb64(wrapped["ct"]), f"ragnarok/{VERSION}/{label}".encode()
        )
    except InvalidTag as exc:
        raise WrongSecret("wrong passphrase or recovery code") from exc


def _check_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE:
        raise KeystoreError(f"passphrase must be at least {MIN_PASSPHRASE} characters")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KeystoreError("keystore is missing or unreadable") from exc
    if doc.get("version") != VERSION:
        raise KeystoreError(f"unsupported keystore version: {doc.get('version')}")
    return doc


def _passphrase_wrap(passphrase: str, vault_key: bytes, kdf: KdfParams) -> dict:
    salt = os.urandom(16)
    kek = _passphrase_key(passphrase, salt, kdf)
    return {
        "salt": _b64(salt),
        "kdf": {
            "time_cost": kdf.time_cost,
            "memory_kib": kdf.memory_kib,
            "parallelism": kdf.parallelism,
        },
        **_wrap(kek, vault_key, "passphrase"),
    }


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(_RECOVERY_BYTES)).decode("ascii")
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def create_keystore(path: Path, passphrase: str, kdf: KdfParams = DEFAULT_KDF) -> CreatedKeys:
    """Generate a new vault key and write its wrapped forms. Refuses to overwrite a keystore."""
    path = Path(path)
    if path.exists():
        raise KeystoreError("keystore already exists")
    _check_passphrase(passphrase)
    vault_key = secrets.token_bytes(VK_BYTES)
    code = _new_recovery_code()
    rec_salt = os.urandom(16)
    doc = {
        "version": VERSION,
        "passphrase": _passphrase_wrap(passphrase, vault_key, kdf),
        "recovery": {
            "salt": _b64(rec_salt),
            **_wrap(_recovery_key(code, rec_salt), vault_key, "recovery"),
        },
    }
    _write_atomic(path, json.dumps(doc, indent=2).encode("utf-8"))
    return CreatedKeys(vault_key=vault_key, recovery_code=code)


def unlock_with_passphrase(path: Path, passphrase: str) -> bytes:
    p = _read(Path(path))["passphrase"]
    kdf = KdfParams(p["kdf"]["time_cost"], p["kdf"]["memory_kib"], p["kdf"]["parallelism"])
    return _unwrap(_passphrase_key(passphrase, _unb64(p["salt"]), kdf), p, "passphrase")


def unlock_with_recovery(path: Path, code: str) -> bytes:
    r = _read(Path(path))["recovery"]
    return _unwrap(_recovery_key(code, _unb64(r["salt"])), r, "recovery")


def change_passphrase(path: Path, old: str, new: str, kdf: KdfParams = DEFAULT_KDF) -> None:
    """Re-wrap the same vault key under a new passphrase. The vault file is untouched."""
    _check_passphrase(new)
    vault_key = unlock_with_passphrase(path, old)
    _replace_passphrase(Path(path), vault_key, new, kdf)


def reset_passphrase_with_recovery(
    path: Path, code: str, new: str, kdf: KdfParams = DEFAULT_KDF
) -> None:
    _check_passphrase(new)
    vault_key = unlock_with_recovery(path, code)
    _replace_passphrase(Path(path), vault_key, new, kdf)


def _replace_passphrase(path: Path, vault_key: bytes, new: str, kdf: KdfParams) -> None:
    doc = _read(path)
    doc["passphrase"] = _passphrase_wrap(new, vault_key, kdf)
    _write_atomic(path, json.dumps(doc, indent=2).encode("utf-8"))


# ---- optional DPAPI auto-unlock (Windows only) -------------------------------------------------


def _win32crypt():
    if sys.platform != "win32":
        raise KeystoreError("DPAPI is only available on Windows")
    import win32crypt

    return win32crypt


def enable_dpapi(dpapi_path: Path, vault_key: bytes) -> None:
    """Seal the vault key to the current Windows user. Anyone logged in as them can unlock."""
    blob = _win32crypt().CryptProtectData(
        vault_key, "ragnarok vault key", DPAPI_ENTROPY, None, None, 0
    )
    _write_atomic(Path(dpapi_path), blob)


def unlock_with_dpapi(dpapi_path: Path) -> bytes:
    try:
        blob = Path(dpapi_path).read_bytes()
        _, key = _win32crypt().CryptUnprotectData(blob, DPAPI_ENTROPY, None, None, 0)
    except OSError as exc:
        raise KeystoreError("DPAPI copy is missing") from exc
    except Exception as exc:  # pywintypes.error: not this user, or a damaged blob
        raise WrongSecret("DPAPI copy cannot be opened by this user") from exc
    return key


def disable_dpapi(dpapi_path: Path) -> None:
    Path(dpapi_path).unlink(missing_ok=True)
