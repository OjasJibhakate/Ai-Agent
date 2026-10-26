"""The sandbox against a hostile script: every bad attempt must be stopped (FR2.3)."""

import json
import os
from pathlib import Path

import pytest

from ragnarok.sandbox.runner import SandboxRefused, build_argv, run_sandboxed, scrubbed_env

EVIL = Path(__file__).parent.parent / "fixtures" / "evil-skill"
ARGV = ["python", "scripts/evil.py"]


async def attempt(tmp_path, what, *, deny=None, timeout=20, **extra):
    return await run_sandboxed(
        ARGV,
        json.dumps({"attempt": what, **extra}),
        cwd=EVIL,
        workdir=tmp_path / "work",
        timeout_s=timeout,
        deny=deny,
    )


def report(result):
    assert result.ok, result.stderr
    return json.loads(result.stdout)


async def test_reading_a_denied_file_is_blocked(tmp_path):
    secret = tmp_path / "vault.db"
    secret.write_bytes(b"not for skills")
    out = report(await attempt(tmp_path, "read_denied", path=str(secret), deny=[str(secret)]))
    assert out["blocked"] and "off limits" in out["detail"]


async def test_a_network_connection_is_blocked_while_offline(tmp_path):
    out = report(await attempt(tmp_path, "network"))
    assert out["blocked"] and "no network" in out["detail"]


async def test_ctypes_cannot_be_loaded(tmp_path):
    out = report(await attempt(tmp_path, "ctypes"))
    assert out["blocked"] and "ctypes" in out["detail"]


async def test_writing_outside_the_work_folder_is_blocked(tmp_path):
    target = tmp_path / "elsewhere.txt"
    out = report(await attempt(tmp_path, "write_outside", path=str(target)))
    assert out["blocked"] and not target.exists()


async def test_starting_another_program_is_blocked(tmp_path):
    out = report(await attempt(tmp_path, "spawn"))
    assert out["blocked"] and "other programs" in out["detail"]


async def test_writing_inside_the_work_folder_is_allowed(tmp_path):
    out = report(await attempt(tmp_path, "write_inside"))
    assert not out["blocked"]
    assert (tmp_path / "work" / "ok.txt").read_text() == "fine"


async def test_secrets_in_the_environment_do_not_reach_the_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hunter2")
    monkeypatch.setenv("RAGNAROK_TEST_TOKEN", "abc")
    out = report(await attempt(tmp_path, "env"))
    assert out["detail"] == "{}"


async def test_a_script_that_never_ends_is_stopped_at_the_time_limit(tmp_path):
    result = await attempt(tmp_path, "sleep", timeout=2)
    assert not result.ok and result.error_class == "timeout" and result.seconds < 10


async def test_a_memory_hog_is_stopped_by_the_job_object(tmp_path):
    result = await attempt(tmp_path, "memory")
    if result.limits != "job":
        pytest.skip("Windows did not give this process a Job Object")
    # either it caught the MemoryError and reported it, or the process was killed
    assert (result.ok and json.loads(result.stdout)["blocked"]) or not result.ok


async def test_output_is_capped(tmp_path):
    result = await attempt(tmp_path, "flood")
    assert result.ok is False or len(result.stdout) <= 20_000


def test_shells_and_escaping_scripts_are_refused():
    for bad in (["powershell", "-c", "dir"], ["cmd", "/c", "dir"], ["bash", "-c", "ls"]):
        with pytest.raises(SandboxRefused):
            build_argv(bad, EVIL)
    with pytest.raises(SandboxRefused):
        build_argv(["python", "../../etc/x.py"], EVIL)
    with pytest.raises(SandboxRefused):
        build_argv(["python", "-c", "print(1)"], EVIL)


def test_the_environment_is_scrubbed_and_proxies_point_at_a_closed_port(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "x")
    env = scrubbed_env(tmp_path, offline=True, deny=[], write_roots=[])
    assert "MY_API_KEY" not in env and env["HTTPS_PROXY"].endswith(":9")
    assert (env["RAGNAROK_OFFLINE"] == "1" and "SYSTEMROOT" in env) or os.name != "nt"
