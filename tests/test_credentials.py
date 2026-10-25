"""Passwords and guessed web addresses: what the screen skill refuses to type, and what is stored.

The passwords here are made up. A real one must never be written into a test.
"""

import pytest

from ragnarok.memory.store import scrub_secrets
from ragnarok.skills.screen.input import (
    InputRefused,
    check_text,
    looks_like_password,
    user_secrets,
)

ASK = "this is my id:-24070521018 and this is password:-Qz7Lm2Xv9T#KP@4 go to the lms and login"


@pytest.mark.parametrize(
    "text",
    ["Qz7Lm2Xv9T#KP@4", "Tr0ub4dor&3xyz", "aB3$eFgH9!", "p@ssW0rd-Long-Enough!"],
)
def test_a_password_shaped_string_is_never_typed_even_without_a_label(text):
    assert looks_like_password(text)
    with pytest.raises(InputRefused, match="password or PIN"):
        check_text(text, "log me in")


@pytest.mark.parametrize(
    "text",
    ["notepad", "hello world", "24070521018", "Hello World 2024", "report.pdf", "john.doe@mail.com",
     "Dr Rao's appointment", "sho1A!x", "a" * 30],
)  # fmt: skip
def test_ordinary_text_is_still_typed(text):
    assert not looks_like_password(text)
    check_text(text, "type it please")


def test_whatever_the_user_labelled_as_a_password_is_not_typed_even_if_it_looks_plain():
    message = "my password is tiger77 and my pin: 4821, log in"
    assert user_secrets(message) == ["tiger77", "4821"]
    for secret in ("tiger77", "4821"):
        with pytest.raises(InputRefused, match="password or PIN"):
            check_text(secret, message)
    check_text("24070521018", ASK)  # the id is not a secret; only the labelled value is
    with pytest.raises(InputRefused):
        check_text("Qz7Lm2Xv9T#KP@4", ASK)


def test_the_labelled_value_is_found_with_the_odd_separators_people_use():
    assert user_secrets("password:-abc12345") == ["abc12345"]
    assert user_secrets("pwd = hunter22xx.") == ["hunter22xx"]
    assert user_secrets("passcode - 998877") == ["998877"]
    assert user_secrets("otp is 123456") == ["123456"]
    assert user_secrets("no secrets here") == []


def test_a_web_address_is_typed_only_when_the_user_wrote_it():
    guess = "sitnagpur.lms.intra.sutd.edu.sg"
    with pytest.raises(InputRefused, match="do not guess"):
        check_text(guess, "go to the sit nagpur lms and see my attendance")
    with pytest.raises(InputRefused, match="do not guess"):
        check_text("https://www.made-up-college.edu/login", "open my college site")
    check_text("lms.example.edu", "please go to lms.example.edu and look")
    check_text("https://lms.example.edu/login", "open https://lms.example.edu/login now")
    check_text("lms.example.edu", "go to https://lms.example.edu/login")  # same address, no scheme


def test_file_names_and_ordinary_words_that_contain_dots_are_not_taken_for_addresses():
    for text in ("report.pdf", "main.py", "notes.v2.txt", "e.g. this", "Mr. Rao", "3.14159"):
        check_text(text, "type it")


# ---- what reaches storage ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,gone",
    [
        (ASK, "Qz7Lm2Xv9T#KP@4"),
        ("my password is Qz7Lm2Xv9T#KP@4", "Qz7Lm2Xv9T#KP@4"),
        ("pwd: hunter22xyz", "hunter22xyz"),
        ("login passcode is 998877", "998877"),
        ("my pin: 4821 ok", "4821"),
        ("otp = 556677", "556677"),
        ("passphrase:- correct-horse", "correct-horse"),
    ],
)
def test_labelled_secrets_never_reach_the_stored_event(message, gone):
    assert gone not in scrub_secrets(message)
    assert "[REDACTED]" in scrub_secrets(message)


def test_the_scrubber_leaves_ordinary_sentences_alone():
    text = "I pin the tab and the token ring was fun; my id is 24070521018"
    assert scrub_secrets("the pin on my map") == "the pin on my map"
    assert "24070521018" in scrub_secrets(text)
