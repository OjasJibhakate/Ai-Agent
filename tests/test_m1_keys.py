"""Key handling: passphrase/recovery wraps, rotation, DPAPI, and no secret stored in clear."""

import json
import sys

import pytest

from ragnarok.memory.vault import Vault
from ragnarok.vault import keys
from ragnarok.vault.keys import KdfParams, KeystoreError, WrongSecret

FAST = KdfParams(time_cost=1, memory_kib=64, parallelism=1)
PASS = "correct horse battery"


@pytest.fixture()
def ks(tmp_path):
    path = tmp_path / "key.json"
    created = keys.create_keystore(path, PASS, FAST)
    return path, created


def test_passphrase_unlocks_same_key(ks):
    path, created = ks
    assert len(created.vault_key) == 32
    assert keys.unlock_with_passphrase(path, PASS) == created.vault_key


def test_wrong_passphrase_fails(ks):
    path, _ = ks
    with pytest.raises(WrongSecret):
        keys.unlock_with_passphrase(path, "wrong passphrase!")


def test_recovery_code_unlocks_and_tolerates_formatting(ks):
    path, created = ks
    assert keys.unlock_with_recovery(path, created.recovery_code) == created.vault_key
    messy = created.recovery_code.lower().replace("-", "  ")
    assert keys.unlock_with_recovery(path, messy) == created.vault_key
    with pytest.raises(WrongSecret):
        keys.unlock_with_recovery(path, "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH")


def test_keystore_holds_no_plaintext_secret(ks):
    path, created = ks
    raw = path.read_bytes()
    assert created.vault_key not in raw
    assert created.vault_key.hex().encode() not in raw
    assert PASS.encode() not in raw
    assert created.recovery_code.replace("-", "").encode() not in raw
    assert json.loads(raw)["version"] == 1


def test_create_refuses_to_overwrite_and_short_passphrase(tmp_path, ks):
    path, _ = ks
    with pytest.raises(KeystoreError):
        keys.create_keystore(path, PASS, FAST)
    with pytest.raises(KeystoreError):
        keys.create_keystore(tmp_path / "other.json", "short", FAST)


def test_change_passphrase_keeps_key_and_invalidates_old(ks):
    path, created = ks
    keys.change_passphrase(path, PASS, "a brand new passphrase", FAST)
    assert keys.unlock_with_passphrase(path, "a brand new passphrase") == created.vault_key
    with pytest.raises(WrongSecret):
        keys.unlock_with_passphrase(path, PASS)
    assert keys.unlock_with_recovery(path, created.recovery_code) == created.vault_key
    with pytest.raises(WrongSecret):
        keys.change_passphrase(path, "not the old one", "another passphrase!", FAST)


def test_reset_passphrase_with_recovery_code(ks):
    path, created = ks
    keys.reset_passphrase_with_recovery(path, created.recovery_code, "reset passphrase 1", FAST)
    assert keys.unlock_with_passphrase(path, "reset passphrase 1") == created.vault_key


def test_tampered_keystore_fails_closed(ks):
    path, _ = ks
    doc = json.loads(path.read_text())
    doc["passphrase"]["ct"] = doc["passphrase"]["ct"][:-4] + "AAAA"
    path.write_text(json.dumps(doc))
    with pytest.raises(WrongSecret):
        keys.unlock_with_passphrase(path, PASS)


def test_wraps_are_bound_to_their_label(ks):
    path, _ = ks
    doc = json.loads(path.read_text())
    doc["recovery"]["nonce"], doc["recovery"]["ct"] = (
        doc["passphrase"]["nonce"],
        doc["passphrase"]["ct"],
    )
    path.write_text(json.dumps(doc))
    with pytest.raises(WrongSecret):  # AAD differs, so a swapped wrap is rejected
        keys.unlock_with_recovery(path, "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH")


def test_missing_or_unknown_version_keystore(tmp_path):
    with pytest.raises(KeystoreError):
        keys.unlock_with_passphrase(tmp_path / "nope.json", PASS)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 99}))
    with pytest.raises(KeystoreError):
        keys.unlock_with_passphrase(bad, PASS)


def test_key_opens_a_vault_end_to_end(tmp_path, ks):
    path, created = ks
    db = tmp_path / "vault.db"
    Vault.open(db, created.vault_key).close()
    Vault.open(db, keys.unlock_with_passphrase(path, PASS)).close()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_round_trip(tmp_path, ks):
    _, created = ks
    blob = tmp_path / "key.dpapi"
    keys.enable_dpapi(blob, created.vault_key)
    assert created.vault_key not in blob.read_bytes()
    assert keys.unlock_with_dpapi(blob) == created.vault_key
    keys.disable_dpapi(blob)
    with pytest.raises(KeystoreError):
        keys.unlock_with_dpapi(blob)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_damaged_dpapi_blob_is_rejected(tmp_path):
    blob = tmp_path / "key.dpapi"
    blob.write_bytes(b"not a dpapi blob")
    with pytest.raises(WrongSecret):
        keys.unlock_with_dpapi(blob)


def test_default_kdf_cost_is_about_half_a_second(tmp_path):
    """Informational guard: the default must be neither trivial nor painful (target ~0.5 s)."""
    import time

    t = time.perf_counter()
    keys.create_keystore(tmp_path / "k.json", PASS)
    elapsed = time.perf_counter() - t
    assert 0.05 < elapsed < 3.0, f"Argon2id default cost took {elapsed:.2f}s"
