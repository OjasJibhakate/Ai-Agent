"""Skill registration (FR2.1): validation, tests-before-register, approval, tamper check, tools."""

import json
import shutil
from pathlib import Path

import pytest

from ragnarok.memory.vault import Vault
from ragnarok.skills.registry import (
    SkillError,
    SkillRegistry,
    check_expect,
    folder_hash,
    load_skill,
)

ROOT = Path(__file__).parent.parent
TEXT_STATS = ROOT / "skills" / "text-stats"
EVIL = ROOT / "fixtures" / "evil-skill"


@pytest.fixture
def registry(tmp_path):
    vault = Vault.open(tmp_path / "vault.db", bytes(range(32)))
    yield SkillRegistry(vault, tmp_path, reserved=lambda: {"memory_search"})
    vault.close()


async def yes(_summary: str) -> bool:
    return True


async def no(_summary: str) -> bool:
    return False


def copy_skill(tmp_path: Path, src: Path = TEXT_STATS, name: str = "copy") -> Path:
    dest = tmp_path / name
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def edit_manifest(folder: Path, change) -> None:
    path = folder / "skill.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


# ---- reading and validating ---------------------------------------------------------------------


def test_the_shipped_skill_loads():
    skill = load_skill(TEXT_STATS)
    assert skill.name == "text-stats" and skill.manifest.tools[0].name == "text_stats"


def test_a_folder_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: x1\ndescription: d\n---\n")
    with pytest.raises(SkillError, match=r"skill.json"):
        load_skill(tmp_path)


def test_a_bad_name_or_header_is_refused(tmp_path):
    folder = copy_skill(tmp_path)
    (folder / "SKILL.md").write_text("---\nname: Bad Name\ndescription: d\n---\n")
    with pytest.raises(SkillError, match="name"):
        load_skill(folder)
    (folder / "SKILL.md").write_text("no header here")
    with pytest.raises(SkillError, match="header"):
        load_skill(folder)


def test_manifest_mistakes_are_refused(tmp_path):
    def bad_type(d):
        d["tools"][0]["input"]["properties"]["text"]["type"] = "array"

    def unknown_test_tool(d):
        d["tests"][0]["tool"] = "nope"

    def missing_script(d):
        d["tools"][0]["run"] = ["python", "scripts/missing.py"]

    for change in (bad_type, unknown_test_tool, missing_script):
        folder = copy_skill(tmp_path, name=f"c-{change.__name__}")
        edit_manifest(folder, change)
        with pytest.raises(SkillError):
            load_skill(folder)


def test_the_hash_changes_when_a_file_changes(tmp_path):
    folder = copy_skill(tmp_path)
    before = folder_hash(folder)
    assert folder_hash(folder) == before
    (folder / "scripts" / "stats.py").write_text("print('changed')")
    assert folder_hash(folder) != before


def test_check_expect():
    assert check_expect({"a": 1, "b": 2}, {"a": 1}) is None
    assert "expected 2" in check_expect({"a": 1}, {"a": 2})
    assert check_expect([1], {"a": 1}) is not None


# ---- registering ---------------------------------------------------------------------------------


async def test_a_good_skill_is_tested_approved_and_registered(registry):
    seen = []

    async def approve(summary: str) -> bool:
        seen.append(summary)
        return True

    record = await registry.add(TEXT_STATS, approve)
    assert record.name == "text-stats" and record.enabled and record.tools == ["text_stats"]
    assert "1 of 1 passed" in seen[0] and "no network" in seen[0]
    assert (registry.root / "text-stats" / "scripts" / "stats.py").is_file()
    assert [r.name for r in registry.list()] == ["text-stats"]


async def test_a_skill_the_owner_does_not_approve_is_not_registered(registry):
    with pytest.raises(SkillError, match="not approved"):
        await registry.add(TEXT_STATS, no)
    assert registry.list() == [] and not (registry.root / "text-stats").exists()


async def test_a_skill_whose_tests_fail_is_not_registered_and_never_asks(registry, tmp_path):
    folder = copy_skill(tmp_path)
    edit_manifest(folder, lambda d: d["tests"][0]["expect"].update(words=999))
    asked = []

    async def approve(summary: str) -> bool:
        asked.append(summary)
        return True

    with pytest.raises(SkillError, match="tests failed"):
        await registry.add(folder, approve)
    assert asked == [] and registry.list() == []


async def test_a_tool_may_not_shadow_a_built_in(registry, tmp_path):
    folder = copy_skill(tmp_path)

    def rename(d):
        d["tools"][0]["name"] = "memory_search"
        d["tests"][0]["tool"] = "memory_search"

    edit_manifest(folder, rename)
    with pytest.raises(SkillError, match="built-in"):
        await registry.add(folder, yes)


# ---- using it ------------------------------------------------------------------------------------


async def test_a_registered_skill_becomes_a_tool_that_runs_in_the_sandbox(registry):
    await registry.add(TEXT_STATS, yes)
    tools, warnings = registry.tools()
    assert warnings == [] and [t.name for t in tools] == ["text_stats"]
    tool = tools[0]
    assert tool.sandbox == "read-only" and tool.risk.value == "R0" and tool.network == []
    args = tool.args_model.model_validate({"text": "a b a", "top": 1})
    out = await tool.handler(args)
    assert out.startswith('<skill_output skill="text-stats" tool="text_stats">')
    assert '"words": 3' in out and '"top_words": ["a"]' in out


async def test_the_tool_schema_comes_from_the_manifest(registry):
    await registry.add(TEXT_STATS, yes)
    tool = registry.tools()[0][0]
    spec = tool.spec()["function"]
    assert spec["name"] == "text_stats" and spec["parameters"]["required"] == ["text"]
    assert "reason" not in spec["parameters"]["properties"]


async def test_changing_a_skills_files_switches_it_off(registry):
    await registry.add(TEXT_STATS, yes)
    (registry.root / "text-stats" / "scripts" / "stats.py").write_text("print('{}')")
    tools, warnings = registry.tools()
    assert tools == [] and "changed on disk" in warnings[0]
    record = registry.list()[0]
    assert not record.enabled and "add it again" in record.note


async def test_disable_enable_and_remove(registry):
    await registry.add(TEXT_STATS, yes)
    assert registry.set_enabled("text-stats", False)
    assert registry.tools()[0] == []
    assert registry.set_enabled("text-stats", True)
    assert len(registry.tools()[0]) == 1
    assert registry.remove("text-stats") and registry.list() == []
    assert not (registry.root / "text-stats").exists()
    assert not registry.remove("text-stats")


async def test_a_skill_that_errors_returns_an_error_not_a_crash(registry, tmp_path):
    folder = copy_skill(tmp_path)
    (folder / "scripts" / "stats.py").write_text("import sys\nsys.exit(3)\n")
    edit_manifest(folder, lambda d: d.update(tests=[]))
    await registry.add(folder, yes)
    tool = registry.tools()[0][0]
    out = await tool.handler(tool.args_model.model_validate({"text": "x"}))
    assert out.startswith("error: tool_error")


async def test_a_hostile_skill_cannot_reach_the_vault_even_when_registered(registry, tmp_path):
    """The evil fixture's tests ask it to read the vault; the sandbox must stop it."""
    folder = copy_skill(tmp_path, EVIL, "evil")
    vault_file = registry.home / "vault.db"
    edit_manifest(
        folder,
        lambda d: d.update(
            tests=[
                {
                    "tool": "evil",
                    "args": {"attempt": "read_denied", "path": str(vault_file)},
                    "expect": {"blocked": True},
                }
            ]
        ),
    )
    record = await registry.add(folder, yes)
    assert record.tools == ["evil"]  # its test passed because the read was blocked
