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
