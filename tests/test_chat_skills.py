"""/skill in the chat: add (tested and approved first), list, disable, remove, tool appearing."""

import os

import pytest

from ragnarok.skills.registry import SkillRegistry
from tests.test_chat_flow import build_app, shown


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def with_home(app, rt, home):
    """Keep the skills folder inside the test's own folder, never the fake C:\\data."""
    app.skills = SkillRegistry(
        rt.vault, home, reserved=lambda: set(app.agent.tools) - app._skill_names
    )


async def test_adding_a_skill_asks_first_and_makes_its_tool_available(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [], ["/skill add text-stats", "/skill list", "/quit"], answers=("y",)
    )
    with_home(app, rt, tmp_path / "home")
    await app.run()
    text = shown(out)
    assert "Added text-stats" in text and "text_stats" in app.agent.tools
    assert "text-stats v1.0.0 [R0] on" in text


async def test_a_skill_that_is_not_approved_is_not_added(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [], ["/skill add text-stats", "/quit"], answers=("n",)
    )
    with_home(app, rt, tmp_path / "home")
    await app.run()
    assert "not approved" in shown(out) and "text_stats" not in app.agent.tools


async def test_disable_and_remove_take_the_tool_away(tmp_path, vault_key):
    lines = ["/skill add text-stats", "/skill disable text-stats", "/skill enable text-stats",
             "/skill remove text-stats", "/skill list", "/quit"]  # fmt: skip
    app, rt, out, _ = build_app(tmp_path, vault_key, [], lines, answers=("y",))
    with_home(app, rt, tmp_path / "home")
    seen_tools = []
    original = app._refresh_skills

    async def spy():
        await original()
        seen_tools.append("text_stats" in app.agent.tools)

    app._refresh_skills = spy
    await app.run()
    assert seen_tools == [True, False, True, False]  # add, disable, enable, remove
    assert "No skills yet" in shown(out)


async def test_a_missing_folder_and_usage_are_explained(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [], ["/skill add nowhere", "/skill zzz", "/quit"], answers=("y",)
    )
    with_home(app, rt, tmp_path / "home")
    await app.run()
    text = shown(out)
    assert "No skill folder at nowhere" in text and "Usage: /skill" in text
