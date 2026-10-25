"""Permission modes, and the path guard that holds in every one of them."""

import os
from pathlib import Path

import pytest

from ragnarok.policy import permissions
from ragnarok.policy.modes import MODES, PROFILE_OF, Mode
from ragnarok.policy.permissions import Risk
from ragnarok.skills.pathguard import PathGuard, PathRefused

# ---- modes -------------------------------------------------------------------------------------


def decide(mode: str, risk: Risk, tool: str = "some_tool"):
    return permissions.check(PROFILE_OF[mode], tool, risk)


# which risk levels run without asking in each mode
RUNS = {"ask": {Risk.R0, Risk.R1}, "edits": {Risk.R0, Risk.R1, Risk.R2}, "full": set(Risk)}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("risk", list(Risk))
def test_each_mode_runs_or_asks_by_risk(mode, risk):
    runs = risk in RUNS[mode]
    decision = decide(mode, risk)
    assert decision.allowed is runs and decision.ask is (not runs)


def test_moving_the_real_mouse_asks_even_in_full_mode():
    assert decide("full", Risk.R2, "use_computer").ask
    assert not decide("full", Risk.R2, "use_computer").allowed


def test_background_runs_and_screen_tasks_never_get_the_wider_modes():
    assert permissions.check("background", "delete_path", Risk.R3).ask
    assert not permissions.check("screen", "run_command", Risk.R3).allowed
    assert not permissions.check("screen", "delete_path", Risk.R3).allowed


def test_the_default_profile_is_unchanged():
    # existing behaviour: the owner's R0/R1 tools run, R2/R3 ask (mode "ask" is this profile)
    assert PROFILE_OF["ask"] == "interactive"
    assert permissions.check("interactive", "memory_save", Risk.R1).allowed
    assert permissions.check("interactive", "download_file", Risk.R2).ask


def test_mode_names_and_switching():
    mode = Mode()
    assert mode.name == "ask" and mode.profile == "interactive" and not mode.wide
    for name in MODES:
        mode.set(name.upper())
        assert mode.name == name
    assert mode.wide
    with pytest.raises(ValueError, match="ask, edits, full"):
        mode.set("yolo")
    assert mode.name == "full"  # a bad name changes nothing


@pytest.mark.parametrize(
    ("value", "name"),
    [(None, "ask"), ("", "ask"), ("full", "full"), ("EDITS", "edits"), ("nonsense", "ask")],
)
def test_the_starting_mode_comes_from_the_environment_and_defaults_to_safe(value, name):
    assert Mode.from_env(value).name == name


# ---- the path guard --------------------------------------------------------------------------


@pytest.fixture()
def world(tmp_path):
    home = tmp_path / "home"
    for folder in (
        "Desktop",
        "Documents",
        "Downloads",
        "dev",
        ".ssh",
        "AppData/Local",
        "OneDrive/Desktop",
    ):
        (home / folder).mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    return home, vault, PathGuard(home, [vault])


def test_ordinary_places_in_the_profile_are_writable(world):
    home, _, guard = world
    for text in (
        "Desktop/a.txt",
        "dev/new/deep/file.py",
        "Documents",
        str(home / "Downloads" / "x"),
    ):
        assert guard.writable(text)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (".ssh/id_rsa", "saved logins"),
        (".ssh", "saved logins"),
        ("AppData/Local/anything", "saved logins"),
        (".claude/session.json", "saved logins"),
        (".codex/auth.json", "saved logins"),
        (".aws/credentials", "saved logins"),
    ],
)
def test_other_programs_private_folders_are_never_touched(world, text, why):
    with pytest.raises(PathRefused, match=why):
        world[2].writable(text)


def test_the_vault_is_off_limits_however_it_is_spelled(world):
    home, vault, guard = world
    spellings = [
        str(vault),
        str(vault / "vault.db"),
        str(vault / "sub" / "keys.json"),
        str(home / ".." / "vault" / "vault.db"),
        str(vault).replace("\\", "/"),
    ]
    for text in spellings:
        with pytest.raises(PathRefused, match="vault"):
            guard.writable(text)
        with pytest.raises(PathRefused, match="vault"):
            guard.readable(text, [home], wide=True)


def test_the_system_and_other_locations_are_refused(world):
    home, _, guard = world
    outside = [
        "C:/Windows/System32/x.dll",
        "C:/Program Files/app/x",
        "C:/",
        "C:\\",
        str(home.parent / "elsewhere.txt"),
        "C:/Users/someone-else/Documents/x",
    ]
    for text in outside:
        with pytest.raises(PathRefused):
            guard.writable(text)


def test_dot_dot_and_links_cannot_lead_out(world, tmp_path):
    home, vault, guard = world
    with pytest.raises(PathRefused):
        guard.writable("Desktop/../../vault/vault.db")
    link = home / "Desktop" / "shortcut"
    try:
        os.symlink(vault, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    with pytest.raises(PathRefused, match="vault"):
        guard.writable("Desktop/shortcut/vault.db")


@pytest.mark.parametrize("text", ["", "   ", "\x00", "x" * 401, '""'])
def test_unusable_paths_are_refused(world, text):
    with pytest.raises(PathRefused):
        world[2].resolve(text)


def test_reading_is_limited_to_the_roots_unless_wide(world):
    home, _, guard = world
    roots = [home / "Desktop", home / "Documents", home / "Downloads"]
    (home / "dev" / "code.py").write_text("x")
    with pytest.raises(PathRefused, match="/mode edits"):
        guard.readable("dev/code.py", roots, wide=False)
    assert guard.readable("dev/code.py", roots, wide=True)
    assert guard.readable("Desktop/anything.txt", roots, wide=False)
    with pytest.raises(PathRefused):  # protected even when wide
        guard.readable(".ssh/id_rsa", roots, wide=True)


def test_files_named_like_secrets_are_not_read(world):
    home, _, guard = world
    for name in ("passwords.txt", ".env", "my_token.txt", "server.pem", "id_ed25519"):
        with pytest.raises(PathRefused, match="secrets"):
            guard.readable(f"Desktop/{name}", [home / "Desktop"], wide=False)


def test_the_profile_folders_themselves_cannot_be_moved_or_deleted(world):
    home, _, guard = world
    for text in (str(home), "Desktop", "Documents", "Downloads", "OneDrive"):
        with pytest.raises(PathRefused, match="itself"):
            guard.whole_folder_ok(guard.resolve(text), "delete")
    guard.whole_folder_ok(guard.resolve("Desktop/project"), "delete")  # things inside are fine


def test_a_drive_root_is_never_usable(world):
    with pytest.raises(PathRefused, match="drive root"):
        world[2].writable("D:/")


def test_known_folders_are_used_for_the_real_profile_only(world):
    assert world[2].known == {}  # a test home never picks up the real Documents
    real = PathGuard()
    assert isinstance(real.home, Path)
    docs = real.resolve("Documents/Ragnarok/x.txt")
    assert docs.name == "x.txt" and docs.parent.name == "Ragnarok"
