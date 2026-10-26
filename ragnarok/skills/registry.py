"""Skill registration and execution (FR2.1, FR2.3).

A skill is a folder with:
- `SKILL.md`: a flat header (`name:` and `description:` between `---` lines) and instructions;
- `skill.json`: version, risk, tools (each with a JSON-schema-like input, the command to run, its
  sandbox mode, network need and time limit) and tests (a tool, arguments, the expected result);
- the scripts the tools run.

Registering one:
1. validate the manifest;
2. run its tests in the sandbox (a skill whose tests fail is not registered);
3. show the owner what it may do and ask;
4. copy it to `<home>/skills/<name>/` and store its manifest and a content hash in the vault.

On every start the hash is checked: if the files changed, the skill is switched off until it is
added again and approved again. Nothing changes silently.

The header is flat (no YAML library is installed and adding one needs approval); the structured
part is JSON. See D-047.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model, field_validator

from ragnarok.memory.store import scrub_secrets
from ragnarok.memory.vault import Vault
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.sandbox.runner import SandboxResult, run_sandboxed

NAME = re.compile(r"^[a-z][a-z0-9_-]{1,38}$")
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
MAX_TOOLS = 8
TYPES = {"string": str, "integer": int, "number": float, "boolean": bool}
SKIP_PARTS = {"__pycache__"}


class SkillError(Exception):
    """The skill cannot be registered or run; the message says why, in plain words."""


class ToolSpec(BaseModel):
    name: str
    description: str = Field(max_length=300)
    run: list[str] = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    sandbox: Literal["read-only", "workspace-write"] = "read-only"
    network: list[str] = Field(default_factory=list)
    timeout_s: float = Field(default=20, gt=0, le=120)
    idempotent: bool = True
    risk: Literal["R0", "R1", "R2"] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not TOOL_NAME.match(v):
            raise ValueError("tool names are lower-case letters, digits and underscores")
        return v

    @field_validator("input")
    @classmethod
    def _input(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") != "object":
            raise ValueError("input must be an object schema")
        for key, prop in (v.get("properties") or {}).items():
            if prop.get("type") not in TYPES:
                raise ValueError(f"input {key!r}: type must be one of {sorted(TYPES)}")
        for key in v.get("required") or []:
            if key not in (v.get("properties") or {}):
                raise ValueError(f"required input {key!r} is not declared")
        return v


class TestSpec(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    version: str = "0.1.0"
    risk: Literal["R0", "R1", "R2"] = "R1"
    tools: list[ToolSpec] = Field(min_length=1, max_length=MAX_TOOLS)
    tests: list[TestSpec] = Field(default_factory=list)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    manifest: Manifest
    folder: Path


@dataclass(frozen=True)
class SkillRecord:
    name: str
    version: str
    description: str
    risk: str
    enabled: bool
    note: str
    tools: list[str]


# ---- reading a folder ------------------------------------------------------------------------


def parse_header(text: str) -> tuple[dict[str, str], str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with a --- header holding name: and description:")
    header: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return header, "\n".join(lines[i + 1 :]).strip()
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip().lower()] = value.strip()
    raise SkillError("SKILL.md header is not closed with ---")


def load_skill(folder: Path) -> Skill:
    folder = Path(folder)
    md, js = folder / "SKILL.md", folder / "skill.json"
    if not md.is_file() or not js.is_file():
        raise SkillError("a skill folder needs SKILL.md and skill.json")
    header, body = parse_header(md.read_text(encoding="utf-8"))
    name, description = header.get("name", ""), header.get("description", "")
    if not NAME.match(name):
        raise SkillError("name: must be lower-case letters, digits, - or _ (2 to 39 characters)")
    if not description or len(description) > 300:
        raise SkillError("description: is required and at most 300 characters")
    try:
        manifest = Manifest.model_validate(json.loads(js.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SkillError(f"skill.json is not valid: {_short(exc)}") from exc
    names = [t.name for t in manifest.tools]
    if len(set(names)) != len(names):
        raise SkillError("two tools have the same name")
    for test in manifest.tests:
        if test.tool not in names:
            raise SkillError(f"a test names the unknown tool {test.tool!r}")
    for tool in manifest.tools:
        script = tool.run[0] if tool.run[0] not in ("python", "python3") else None
        if script is None and (len(tool.run) < 2 or not (folder / tool.run[1]).is_file()):
            raise SkillError(f"tool {tool.name}: the script {tool.run[1:2]} is not in the folder")
    return Skill(name, description, body, manifest, folder)


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
    return str(exc)


def folder_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        if SKIP_PARTS & set(path.parts) or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def args_model_for(tool: ToolSpec) -> type[ToolArgs]:
    props = tool.input.get("properties") or {}
    required = set(tool.input.get("required") or [])
    fields: dict[str, Any] = {}
    for key, prop in props.items():
        py = TYPES[prop["type"]]
        if "enum" in prop:
            py = Literal[tuple(prop["enum"])]  # type: ignore[valid-type]
        info = Field(... if key in required else None, description=prop.get("description", ""))
        fields[key] = (py if key in required else py | None, info)
    return create_model(f"Args_{tool.name}", __base__=ToolArgs, **fields)


def check_expect(got: Any, expect: dict[str, Any]) -> str | None:
    """None if every expected key matches in the tool's JSON result, else what differed."""
    if not isinstance(got, dict):
        return f"expected a JSON object, got {type(got).__name__}"
    for key, want in expect.items():
        if got.get(key) != want:
            return f"{key}: expected {want!r}, got {got.get(key)!r}"
    return None


def default_deny(home: Path) -> list[str]:
    """Places no skill may read, whatever it declares."""
    user = Path.home()
    paths = [home / "vault.db", home / "key.json", home / "key.dpapi", home / "logs"]
    paths += [user / ".ssh", user / ".aws", user / ".azure", user / ".gnupg", user / ".claude"]
    paths += [
        user / "AppData" / "Local" / "Google",
        user / "AppData" / "Local" / "Microsoft" / "Edge",
    ]
    paths += [
        user / "AppData" / "Roaming" / "Mozilla",
        user / "AppData" / "Roaming" / "Microsoft" / "Credentials",
    ]
    return [str(p) for p in paths]


@dataclass(frozen=True)
class TestOutcome:
    tool: str
    ok: bool
    detail: str


Approve = Callable[[str], Awaitable[bool]]


class SkillRegistry:
    def __init__(
        self,
        vault: Vault,
        home: Path,
        *,
        reserved: Callable[[], set[str]] = set,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.vault, self.home = vault, Path(home)
        self.reserved = reserved  # names of tools that already exist and may not be shadowed
        self.clock = clock
        self.root = self.home / "skills"
        self.data = self.home / "skill-data"

    def _now(self) -> str:
        return self.clock().replace(microsecond=0).isoformat(sep=" ")

    # ---- running ---------------------------------------------------------------------------------

    async def run_tool(
        self, skill_dir: Path, name: str, spec: ToolSpec, args: dict
    ) -> SandboxResult:
        write_roots: list[str] = []
        scratch = None
        if spec.sandbox == "workspace-write":
            persistent = self.data / name
            persistent.mkdir(parents=True, exist_ok=True)
            workdir, write_roots = persistent, [str(persistent)]
        else:
            scratch = Path(tempfile.mkdtemp(prefix="skill-run-"))
            workdir = scratch
        try:
            return await run_sandboxed(
                spec.run,
                json.dumps(args),
                cwd=skill_dir,
                workdir=workdir,
                timeout_s=spec.timeout_s,
                offline=not spec.network,
                deny=default_deny(self.home),
                write_roots=write_roots,
            )
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)

    async def run_tests(self, skill: Skill) -> list[TestOutcome]:
        outcomes = []
        specs = {t.name: t for t in skill.manifest.tools}
        for test in skill.manifest.tests:
            result = await self.run_tool(skill.folder, skill.name, specs[test.tool], test.args)
            if not result.ok:
                outcomes.append(
                    TestOutcome(test.tool, False, f"{result.error_class}: {result.stderr[:200]}")
                )
                continue
            try:
                got = json.loads(result.stdout)
            except json.JSONDecodeError:
                outcomes.append(TestOutcome(test.tool, False, "the result was not JSON"))
                continue
            problem = check_expect(got, test.expect)
            outcomes.append(TestOutcome(test.tool, problem is None, problem or "passed"))
        return outcomes

    # ---- registering ----------------------------------------------------------------------------

    def summary(self, skill: Skill, outcomes: list[TestOutcome]) -> str:
        lines = [
            f"Skill {skill.name} v{skill.manifest.version} (risk {skill.manifest.risk}): "
            f"{skill.description}",
            f"Tests: {sum(o.ok for o in outcomes)} of {len(outcomes)} passed in the sandbox.",
        ]
        for tool in skill.manifest.tools:
            net = f"network: {', '.join(tool.network)}" if tool.network else "no network"
            where = (
                "writes only to its own data folder"
                if tool.sandbox == "workspace-write"
                else ("cannot write anywhere except a temporary folder")
            )
            lines.append(f"  - {tool.name}: {where}; {net}; limit {tool.timeout_s:g} s")
        lines.append("It runs as a restricted child process and cannot read Ragnarok's own data.")
        return "\n".join(lines)

    async def add(self, source: Path, approve: Approve) -> SkillRecord:
        source = Path(source)
        skill = await asyncio.to_thread(load_skill, source)
        clash = {t.name for t in skill.manifest.tools} & self.reserved()
        if clash:
            raise SkillError(f"tool name(s) already used by built-in tools: {sorted(clash)}")
        outcomes = await self.run_tests(skill)
        failed = [o for o in outcomes if not o.ok]
        if failed:
            raise SkillError(
                "its own tests failed, so it was not registered: "
                + "; ".join(f"{o.tool}: {o.detail}" for o in failed)
            )
        if not await approve(self.summary(skill, outcomes)):
            raise SkillError("not approved")
        target = self.root / skill.name
        await asyncio.to_thread(self._install, source, target)
        digest = await asyncio.to_thread(folder_hash, target)
        now = self._now()
        with self.vault.transaction() as con:
            con.execute("DELETE FROM skills WHERE name = ?", (skill.name,))
            con.execute(
                "INSERT INTO skills (name, version, description, risk, manifest, content_hash,"
                " approved_at, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill.name,
                    skill.manifest.version,
                    scrub_secrets(skill.description),
                    skill.manifest.risk,
                    skill.manifest.model_dump_json(),
                    digest,
                    now,
                    now,
                ),
            )
        return self._record(skill.name)  # type: ignore[return-value]

    def _install(self, source: Path, target: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        stage = self.root / f".stage-{uuid.uuid4().hex[:8]}"
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if target.exists():
            shutil.rmtree(target)
        stage.rename(target)

    # ---- managing -------------------------------------------------------------------------------

    def _record(self, name: str) -> SkillRecord | None:
        rows = self.vault.query(
            "SELECT name, version, description, risk, enabled, note, manifest FROM skills"
            " WHERE name = ?",
            (name,),
        )
        return self._to_record(rows[0]) if rows else None

    @staticmethod
    def _to_record(r: tuple) -> SkillRecord:
        tools = [t["name"] for t in json.loads(r[6]).get("tools", [])]
        return SkillRecord(r[0], r[1], r[2], r[3], bool(r[4]), r[5], tools)

    def list(self) -> list[SkillRecord]:
        rows = self.vault.query(
            "SELECT name, version, description, risk, enabled, note, manifest FROM skills"
            " ORDER BY name"
        )
        return [self._to_record(r) for r in rows]

    def set_enabled(self, name: str, enabled: bool, note: str = "") -> bool:
        with self.vault.transaction() as con:
            cur = con.execute(
                "UPDATE skills SET enabled = ?, note = ? WHERE name = ?",
                (int(enabled), note, name),
            )
        return cur.rowcount > 0

    def remove(self, name: str) -> bool:
        with self.vault.transaction() as con:
            cur = con.execute("DELETE FROM skills WHERE name = ?", (name,))
        shutil.rmtree(self.root / name, ignore_errors=True)
        return cur.rowcount > 0

    # ---- turning skills into tools -----------------------------------------------------------

    def verify(self) -> list[str]:
        """Check every enabled skill's files against the approved hash; switch off the changed."""
        warnings = []
        rows = self.vault.query("SELECT name, content_hash FROM skills WHERE enabled = 1")
        for name, approved in rows:
            folder = self.root / name
            try:
                same = folder.is_dir() and folder_hash(folder) == approved
            except OSError:
                same = False
            if not same:
                self.set_enabled(name, False, "its files changed on disk: add it again to approve")
                warnings.append(f"skill {name} was switched off: its files changed on disk")
        return warnings

    def tools(self) -> tuple[list[Tool], list[str]]:
        warnings = self.verify()
        out: list[Tool] = []
        taken = self.reserved()
        for name, manifest_json, skill_risk in self.vault.query(
            "SELECT name, manifest, risk FROM skills WHERE enabled = 1 ORDER BY name"
        ):
            manifest = Manifest.model_validate_json(manifest_json)
            for spec in manifest.tools:
                if spec.name in taken:
                    warnings.append(f"skill {name}: tool {spec.name} shadows a built-in; skipped")
                    continue
                taken.add(spec.name)
                out.append(self._tool(name, spec, Risk(spec.risk or skill_risk)))
        return out, warnings

    def _tool(self, skill_name: str, spec: ToolSpec, risk: Risk) -> Tool:
        folder = self.root / skill_name

        async def handler(args: ToolArgs) -> str:
            payload = args.model_dump(exclude={"reason"}, exclude_none=True)
            result = await self.run_tool(folder, skill_name, spec, payload)
            if not result.ok:
                return f"error: {result.error_class}: {result.stderr.strip()[:300]}"
            text = result.stdout.strip()
            with_json = text
            try:
                with_json = json.dumps(json.loads(text), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            tag = f'skill="{skill_name}" tool="{spec.name}"'
            return f"<skill_output {tag}>\n{with_json}\n</skill_output>"

        return Tool(
            name=spec.name,
            description=spec.description,
            args_model=args_model_for(spec),
            risk=risk,
            idempotent=spec.idempotent,
            timeout_s=spec.timeout_s + 5,  # the sandbox's own limit fires first
            sandbox=spec.sandbox,
            handler=handler,
            network=list(spec.network),
            approval_note=f"This runs the {skill_name} skill in a restricted process.",
        )
