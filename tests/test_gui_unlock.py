"""Opening the vault from the cat: the passphrase dialog and the "unlock automatically" choice.

The dialog is replaced by a function that answers for the owner. Everything else (the key file, the
Windows DPAPI copy) is real, in a temporary folder. The passphrases here are made up."""

import sys

import pytest

from ragnarok.gui import unlock
from ragnarok.gui.unlock import Answer
from ragnarok.vault import keys
from ragnarok.vault.keys import KdfParams

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

FAST = KdfParams(time_cost=1, memory_kib=64, parallelism=1)
PASS = "made-up passphrase for tests"


@pytest.fixture
def home(tmp_path, monkeypatch):
    original = keys.create_keystore
    monkeypatch.setattr(
        keys, "create_keystore", lambda path, passphrase, kdf=FAST: original(path, passphrase, kdf)
    )
    monkeypatch.setattr(unlock, "_show_recovery", lambda root, code: None)  # no window
    return tmp_path / "ragnarok"


def owner(*answers):
    """A dialog that gives the next answer each time, and remembers what it was shown."""
    queue, shown = list(answers), []

    def ask(_root, title, _heading, *, repeat, automatic, error=""):
        shown.append({"title": title, "repeat": repeat, "automatic": automatic, "error": error})
        return queue.pop(0)

    ask.shown = shown
    ask.left = queue
    return ask


@pytest.fixture
def made(home):
    """A vault that already exists, made without choosing automatic unlock."""
    key = unlock.first_run(None, home, owner(Answer(PASS, False)))
    assert key and not unlock.remembered(home)
    return home, key


def test_first_run_with_the_box_ticked_creates_the_vault_and_remembers_the_account(home):
    ask = owner(Answer(PASS, True))
    key = unlock.first_run(None, home, ask)
    assert key and len(key) == 32
    assert unlock.remembered(home) and keys.unlock_with_dpapi(home / unlock.DPAPI_FILE) == key
    assert ask.shown[0]["repeat"] and ask.shown[0]["automatic"]  # asks twice, offers the box


def test_the_next_start_asks_nothing_once_it_is_remembered(made):
    home, key = made
    assert unlock.remember_on_this_account(home, key) is True
    ask = owner()  # would fail if the dialog were shown: nothing is queued
    assert unlock.unlock(None, home, ask) == key
    assert ask.shown == []


def test_the_dialog_for_an_existing_vault_offers_the_box_and_ticking_it_is_remembered(made):
    home, key = made
    ask = owner(Answer(PASS, True))
    assert unlock.unlock(None, home, ask) == key
    assert ask.shown == [
        {"title": "Unlock Ragnarok", "repeat": False, "automatic": True, "error": ""}
    ]
    assert unlock.remembered(home)
    assert unlock.unlock(None, home, owner()) == key  # and now it does not ask


def test_leaving_the_box_empty_changes_nothing(made):
    home, key = made
    assert unlock.unlock(None, home, owner(Answer(PASS, False))) == key
    assert not unlock.remembered(home)


def test_a_wrong_passphrase_never_turns_it_on_even_with_the_box_ticked(made):
    home, _key = made
    ask = owner(Answer("not it at all", True), Answer("still not", True), Answer("no", True))
    assert unlock.unlock(None, home, ask) is None  # three tries, then it gives up
    assert not unlock.remembered(home) and ask.left == []
    wrong = "That is not the right passphrase."
    assert [s["error"] for s in ask.shown] == ["", wrong, wrong]  # each retry says why


def test_a_right_passphrase_on_the_second_try_still_works_and_remembers(made):
    home, key = made
    ask = owner(Answer("typo", True), Answer(PASS, True))
    assert unlock.unlock(None, home, ask) == key and unlock.remembered(home)


def test_closing_the_dialog_gives_no_key_and_changes_nothing(made):
    home, _key = made
    assert unlock.unlock(None, home, owner(None)) is None
    assert not unlock.remembered(home)


def test_forgetting_goes_back_to_asking_and_the_passphrase_still_works(made):
    home, key = made
    unlock.remember_on_this_account(home, key)
    unlock.forget_this_account(home)
    unlock.forget_this_account(home)  # twice is fine
    ask = owner(Answer(PASS, False))
    assert unlock.unlock(None, home, ask) == key and len(ask.shown) == 1


def test_a_damaged_remembered_file_falls_back_to_asking_and_the_box_repairs_it(made):
    home, key = made
    (home / unlock.DPAPI_FILE).write_bytes(b"not a dpapi blob")
    ask = owner(Answer(PASS, True))
    assert unlock.unlock(None, home, ask) == key and len(ask.shown) == 1
    assert keys.unlock_with_dpapi(home / unlock.DPAPI_FILE) == key  # replaced by a good copy


def test_when_windows_will_not_seal_the_key_the_vault_still_opens(made, monkeypatch):
    home, key = made

    def refuse(*_args):
        raise keys.KeystoreError("DPAPI is only available on Windows")

    monkeypatch.setattr(keys, "enable_dpapi", refuse)
    assert unlock.unlock(None, home, owner(Answer(PASS, True))) == key
    assert not unlock.remembered(home)
    assert unlock.remember_on_this_account(home, key) is False


def test_the_remembered_file_holds_no_plain_key(made):
    home, key = made
    unlock.remember_on_this_account(home, key)
    assert key not in (home / unlock.DPAPI_FILE).read_bytes()
