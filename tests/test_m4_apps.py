"""The open_app skill: name resolution, refusals, and that no model text reaches a command line."""

import pytest

from ragnarok.runtime.gateway import ChatResult
from ragnarok.runtime.loop import Agent, Session
from ragnarok.skills.apps import AppEntry, app_tools, resolve
from tests.test_m4_agent import ScriptedChat, build, key, say  # noqa: F401

APPS = [
    AppEntry("Steam", "{PF}\\Steam\\steam.exe"),
    AppEntry("Steam Support Center", "support-center"),
    AppEntry("Microsoft Edge", "MSEdge"),
    AppEntry("Google Chrome", "Chrome"),
    AppEntry("Notepad", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    AppEntry("Notepad++", "{PF}\\Notepad++\\notepad++.exe"),
    AppEntry("Visual Studio Code", "Microsoft.VisualStudioCode"),
    AppEntry("Visual Studio Installer", "vsinstaller"),
    AppEntry("Uninstall Steam", "uninstall-steam"),
    AppEntry("Windows PowerShell", "powershell-id"),
    AppEntry("Command Prompt", "cmd-id"),
]


def open_app_call(name: str, **extra) -> ChatResult:
    """A model reply asking for open_app (`call` clashes with the `name` argument)."""
    args = {"name": name, **extra}
    return ChatResult(tool_calls=[{"function": {"name": "open_app", "arguments": args}}])


class FakeCatalog:
    def __init__(self):
        self.launched: list[AppEntry] = []

    def list_apps(self):
        return APPS

    def launch(self, app):
        self.launched.append(app)


@pytest.mark.parametrize(
    ("query", "kind", "names"),
    [
        ("steam", "one", ["Steam"]),  # beats "Steam Support Center"
        ("Steam", "one", ["Steam"]),
        ("microsoft edge", "one", ["Microsoft Edge"]),
        ("edge", "one", ["Microsoft Edge"]),
        ("chrome", "one", ["Google Chrome"]),
        ("notepad", "one", ["Notepad"]),  # exact beats Notepad++
        ("visual studio code", "one", ["Visual Studio Code"]),
        ("stean", "one", ["Steam"]),  # typo
        ("visual studio", "many", ["Visual Studio Code", "Visual Studio Installer"]),  # never guess
        ("whatsapp", "none", []),
        ("", "none", []),
    ],
)
def test_resolve(query, kind, names):
    got_kind, found = resolve(query, APPS)
    assert got_kind == kind
    assert sorted(a.name for a in found) == sorted(names)


def test_uninstallers_are_hidden_unless_asked_for():
    assert all("Uninstall" not in a.name for a in resolve("steam", APPS)[1])
    assert resolve("uninstall steam", APPS) == ("one", [APPS[8]])


async def run_tool(name: str):
    catalog = FakeCatalog()
    tool = app_tools(catalog)[0]
    out = await tool.handler(tool.args_model(name=name))
    return out, catalog


async def test_open_app_launches_the_resolved_app():
    out, catalog = await run_tool("steam")
    assert out == "Opened Steam." and [a.name for a in catalog.launched] == ["Steam"]


async def test_open_app_asks_when_ambiguous_and_reports_unknown():
    out, catalog = await run_tool("visual studio")
    assert out.startswith("Several apps match") and catalog.launched == []
    out, catalog = await run_tool("whatsapp")
    assert out.startswith("No installed app matches") and "Steam" in out and catalog.launched == []


@pytest.mark.parametrize(
    "name", ["powershell", "Windows PowerShell", "cmd", "command prompt", "terminal"]
)
async def test_shells_and_admin_tools_are_refused(name):
    out, catalog = await run_tool(name)
    assert "don't open command shells" in out and catalog.launched == []


async def test_only_listed_apps_can_be_started_never_a_path_or_command():
    for evil in ("C:\\Windows\\System32\\calc.exe", "steam && del /q *", "notepad; whoami"):
        out, catalog = await run_tool(evil)
        # whatever the text resolves to, it can only be an entry from the catalog
        assert all(a in APPS for a in catalog.launched), evil


def test_tool_declares_what_the_rules_require():
    tool = app_tools(FakeCatalog())[0]
    assert tool.name == "open_app" and tool.risk.value == "R1" and tool.idempotent is False
    assert tool.sandbox == "full-access" and tool.timeout_s > 0
    spec = tool.spec()["function"]
    assert spec["parameters"]["required"] == ["name"]
    assert tool.description.count(".") <= 2


async def test_agent_opens_the_app_through_the_permission_check_and_logs_it(tmp_path, key):  # noqa: F811
    vault, store, svc, chat, agent = build(
        tmp_path, key, [open_app_call("steam", reason="user asked"), say("Opening Steam.")]
    )
    catalog = FakeCatalog()
    agent.tools.update({t.name: t for t in app_tools(catalog)})
    result = await agent.turn(Session(), "open steam")
    assert [a.name for a in catalog.launched] == ["Steam"]
    assert result.trace[0].rule == "default:R1" and result.trace[0].allowed
    kinds = [r[0] for r in vault.query("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["user_message", "memory_tool_call", "memory_tool_result", "assistant_message"]
    assert "open_app" in [t["function"]["name"] for t in chat.calls[0]["tools"]]
    vault.close()


async def test_background_profile_does_not_open_apps_unasked(tmp_path, key):  # noqa: F811
    vault, store, svc, chat, agent = build(
        tmp_path, key, [open_app_call("steam"), say("blocked")], profile="background"
    )
    catalog = FakeCatalog()
    agent.tools.update({t.name: t for t in app_tools(catalog)})
    result = await agent.turn(Session(), "open steam")
    assert catalog.launched == [] and not result.trace[0].allowed
    vault.close()


async def test_a_launch_failure_is_reported_not_raised(tmp_path, key):  # noqa: F811
    vault, store, svc, chat, agent = build(
        tmp_path, key, [open_app_call("steam"), say("It could not be opened.")]
    )

    class Broken(FakeCatalog):
        def launch(self, app):
            raise OSError("explorer failed")

    agent.tools.update({t.name: t for t in app_tools(Broken())})
    await agent.turn(Session(), "open steam")
    assert chat.calls[1]["messages"][-1]["content"] == "error: OSError"
    vault.close()


def test_unused_imports_are_deliberate():
    assert Agent and ScriptedChat  # keep the shared helpers importable from this module
