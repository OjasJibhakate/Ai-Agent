"""A second `meow` while the cat runs must open the first cat's chat, not start another cat."""

import sys
import uuid

import pytest

from ragnarok.gui import winapi

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses Windows events")


def test_a_summon_is_seen_once_by_the_running_cat():
    name = rf"Local\RagnarokTest{uuid.uuid4().hex}"
    cat = winapi.Summons(name)
    assert not cat.pending()  # nobody called
    winapi.summon(name)
    assert cat.pending()  # the call arrived...
    assert not cat.pending()  # ...and is handled once, not on every frame
    winapi.summon(name)
    winapi.summon(name)
    assert cat.pending()  # (calls that pile up count once, like a doorbell)
    assert not cat.pending()


def test_the_first_copy_wins_and_the_second_knows_it_is_second():
    name = rf"Local\RagnarokTestCopy{uuid.uuid4().hex}"
    assert winapi.only_one_copy(name) is True
    assert winapi.only_one_copy(name) is False  # same process, same name: already taken
