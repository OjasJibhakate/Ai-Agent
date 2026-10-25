"""Starting the cat with Windows: a small hidden launcher in the Startup folder, easy to remove."""

import pytest

from ragnarok import cli
from ragnarok.gui import startup


def test_the_launcher_goes_to_the_project_and_starts_the_cat_without_a_window(tmp_path):
    text = startup.script_text(project=tmp_path / "Ai-Agent", uv=r"C:\tools\uv.exe")
    assert f'shell.CurrentDirectory = "{tmp_path / "Ai-Agent"}"' in text
    assert 'run pythonw -m ragnarok gui", 0, False' in text  # window style 0 = hidden
    assert r'""C:\tools\uv.exe""' in text  # the path is quoted the VBScript way
    assert text.count("\r\n") == 3  # a proper Windows script


def test_install_and_remove_are_idempotent_and_touch_only_their_own_file(tmp_path):
    other = tmp_path / "keep.txt"
    other.write_text("mine")
    path = startup.install(tmp_path, uv="uv")
    assert path == tmp_path / startup.NAME and startup.installed(tmp_path)
    startup.install(tmp_path, uv="uv")  # again: still one file
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([startup.NAME, "keep.txt"])
    assert startup.remove(tmp_path) is True and not startup.installed(tmp_path)
    assert startup.remove(tmp_path) is False and other.read_text() == "mine"


def test_the_default_folder_is_the_users_startup_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = startup.startup_folder()
    assert folder == tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


@pytest.mark.parametrize("flag", ["on", "off"])
def test_the_command_line_switches_it(monkeypatch, tmp_path, capsys, flag):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cli.main(["gui", "--startup", flag]) == 0
    assert startup.installed() is (flag == "on")
    out = capsys.readouterr().out
    assert ("cat will appear" in out) if flag == "on" else ("not set to start" in out)


# ---- the short command ---------------------------------------------------------------------------


def test_meow_is_a_cmd_file_that_runs_the_hidden_launcher_next_to_it(tmp_path):
    written = startup.install_command(tmp_path, project=tmp_path / "Ai-Agent", uv="uv")
    cmd, vbs = tmp_path / "meow.cmd", tmp_path / "meow.vbs"
    assert written == [cmd, vbs]
    assert cmd.read_bytes() == b'@echo off\r\nwscript.exe //nologo "%~dp0meow.vbs"\r\n'
    assert "run pythonw -m ragnarok gui" in vbs.read_text() and ", 0, False" in vbs.read_text()


def test_meow_can_be_removed_and_nothing_else_is_touched(tmp_path):
    other = tmp_path / "other.cmd"
    other.write_text("x")
    startup.install_command(tmp_path, uv="uv")
    assert startup.remove_command(tmp_path) == 2
    assert startup.remove_command(tmp_path) == 0
    assert other.read_text() == "x" and not (tmp_path / "meow.cmd").exists()


def test_the_command_goes_in_a_folder_that_is_already_on_the_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    folder = startup.command_folder()
    assert folder == tmp_path / "Microsoft" / "WindowsApps"
    monkeypatch.setenv("PATH", rf"C:\Windows;{folder}")
    assert startup.on_path()
    monkeypatch.setenv("PATH", r"C:\Windows")
    assert not startup.on_path()


@pytest.mark.parametrize("flag", ["on", "off"])
def test_the_command_line_installs_and_removes_meow(monkeypatch, tmp_path, capsys, flag):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    startup.install_command()  # so that "off" has something to remove
    assert cli.main(["gui", "--command", flag]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "Microsoft" / "WindowsApps" / "meow.cmd").exists() is (flag == "on")
    assert ("Type meow anywhere" in out) if flag == "on" else ("Removed 2 files" in out)
