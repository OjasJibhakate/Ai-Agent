"""The file download skill: what may be fetched, what may be kept, and what a crash leaves behind.

No test here touches the internet: a fake server (httpx.MockTransport) and a fake DNS table do it.
"""

import asyncio
import hashlib
import sys
import types

import httpx
import pytest

from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.runtime.tools import CURRENT_TEXT, PROGRESS
from ragnarok.skills import downloads
from ragnarok.skills.downloads import (
    KNOWN,
    DownloadError,
    DownloadFileArgs,
    clean_name,
    download_tools,
    is_program,
    is_public,
    resolve_source,
    validate_link,
)
from tests.test_m2_memory import key  # noqa: F401  (fixture)
from tests.test_m4_agent import build, call, kinds, say

EXE = b"MZ" + b"\x90" * 5000
PDF = b"%PDF-1.7\n" + b"x" * 3000


class Internet:
    """A fake network: `routes` maps a URL to a response (or a function of the request)."""

    def __init__(self, routes=None, dns=None):
        self.routes = routes or {}
        self.dns = dns or {}
        self.requests: list[httpx.Request] = []

    async def resolver(self, host):
        return self.dns.get(host, ["93.184.216.34"])  # a public address unless said otherwise

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def tool(self, tmp_path):
        return download_tools(httpx.MockTransport(self), tmp_path, self.resolver)[0]


def ok(data=EXE, kind="application/octet-stream", **headers):
    return httpx.Response(200, headers={"content-type": kind, **headers}, content=data)


def redirect(to, status=302):
    return httpx.Response(status, headers={"location": to})


async def run(tool, user_text, source):
    token = CURRENT_TEXT.set(user_text)
    try:
        # model_construct skips the argument check, so these tests reach the handler's own rules
        # (the rules run twice on purpose: before the owner is asked, and again before fetching)
        return await tool.handler(DownloadFileArgs.model_construct(source=source))
    finally:
        CURRENT_TEXT.reset(token)


def text_of(result):
    return getattr(result, "text", result)


STEAM = KNOWN["steam"].url
FOLDER = "Ragnarok"


# ---- what the user may name ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "message", "name"),
    [
        ("steam", "download steam", "steam"),
        ("Steam", "please download STEAM for me", "steam"),
        ("chrome", "download chrome", "chrome"),
        ("google chrome", "download google chrome", "chrome"),
        ("vs code", "get me vs code", "vscode"),
        ("vscode", "download vscode", "vscode"),
        ("python", "download python", "python"),
    ],
)
def test_programs_are_known_by_name_when_the_user_asked_for_them(source, message, name):
    url, label = resolve_source(source, message)
    assert url == KNOWN[name].url and label == KNOWN[name].label


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("steam", "download cat images"),  # injected: the user never mentioned it
        ("steam", "download steamed buns recipe"),  # a longer word is not a mention
        ("chrome", "download chromeos notes"),
    ],
)
def test_a_known_name_the_user_did_not_say_is_refused(source, message):
    with pytest.raises(DownloadError, match="did not mention"):
        resolve_source(source, message)


def test_an_unknown_name_asks_for_a_link_and_lists_what_is_known():
    with pytest.raises(DownloadError) as info:
        resolve_source("photoshop", "download photoshop")
    assert "Paste its download link" in str(info.value) and "steam" in str(info.value)


def test_a_typed_link_is_accepted_and_one_that_was_not_typed_is_refused():
    link = "https://example.com/files/report.pdf"
    assert resolve_source(link, f"download {link} please")[0] == link
    assert resolve_source("example.com/files/report.pdf", f"get {link}")[0] == link
    with pytest.raises(DownloadError, match="typed in your message"):
        resolve_source(link, "download the report")
    with pytest.raises(DownloadError, match="typed in your message"):
        resolve_source("https://evil.example/a.exe", f"download {link}")


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.com/a.zip",
        "ftp://example.com/a.zip",
        "file:///c:/secret.txt",
        "javascript:alert(1)",
        "https://127.0.0.1/a.zip",
        "https://localhost/a.zip",
        "https://192.168.1.5/a.zip",
        "https://[::1]/a.zip",
        "https://user:pass@example.com/a.zip",
        "https://example.com:8443/a.zip",
        "https://example/a.zip",
        "https://exa mple.com/a.zip",
        "https://example.com/a b.zip",
        "https://example.com/" + "a" * 2100,
        "https://example.com/<script>",
        "https://",
    ],
)
def test_odd_links_are_never_fetched(bad):
    with pytest.raises(DownloadError):
        validate_link(bad)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.9", False),
        ("192.168.0.1", False),
        ("169.254.169.254", False),  # cloud metadata address
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),
        ("not an address", False),
    ],
)
def test_only_public_addresses_count(address, public):
    assert is_public(address) is public


# ---- the happy path -----------------------------------------------------------------------------


async def test_download_steam_saves_the_installer_with_a_hash_and_a_warning(tmp_path):
    net = Internet({STEAM: ok(EXE)})
    result = await run(net.tool(tmp_path), "download steam", "steam")
    saved = tmp_path / FOLDER / "SteamSetup.exe"
    assert saved.read_bytes() == EXE
    assert (
        result.final
        and "Saved SteamSetup.exe" in result.text
        and str(tmp_path / FOLDER) in result.text
    )
    assert "It is a program: I have not run it" in result.text
    assert f"SHA-256 {hashlib.sha256(EXE).hexdigest()}" in result.text
    assert not list((tmp_path / FOLDER).glob("*.part"))


async def test_a_typed_link_to_a_document_is_saved_without_the_program_warning(tmp_path):
    link = "https://example.com/files/report.pdf"
    net = Internet({link: ok(PDF, "application/pdf")})
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert (tmp_path / FOLDER / "report.pdf").read_bytes() == PDF
    assert "program" not in result.text


@pytest.mark.skipif(sys.platform != "win32", reason="the internet mark is a Windows feature")
async def test_the_file_is_marked_as_coming_from_the_internet(tmp_path):
    await run(Internet({STEAM: ok(EXE)}).tool(tmp_path), "download steam", "steam")
    with open(f"{tmp_path / FOLDER / 'SteamSetup.exe'}:Zone.Identifier", encoding="ascii") as f:
        mark = f.read()
    assert "ZoneId=3" in mark and "SteamSetup.exe" in mark


async def test_progress_lines_appear_during_a_long_download(tmp_path, monkeypatch):
    ticks = iter(range(0, 1000))
    # a fake `time` for this module only: patching time.monotonic itself would upset the event loop
    fake_time = types.SimpleNamespace(monotonic=lambda: next(ticks) * 0.6)
    monkeypatch.setattr(downloads, "time", fake_time)
    monkeypatch.setattr(downloads, "CHUNK", 1000)
    seen = []

    async def progress(line):
        seen.append(line)

    token = PROGRESS.set(progress)
    try:
        await run(
            Internet({STEAM: ok(EXE, **{"content-length": str(len(EXE))})}).tool(tmp_path),
            "download steam",
            "steam",
        )
    finally:
        PROGRESS.reset(token)
    assert seen, "a download that takes several seconds should say how far it has got"
    assert all(
        s.startswith("downloading SteamSetup.exe: ") and s.endswith(" of 4 KB") for s in seen
    )


# ---- redirects ----------------------------------------------------------------------------------


async def test_a_redirect_to_another_public_host_is_followed(tmp_path):
    # firefox-style: the vendor's address hands over to its download network
    net = Internet(
        {
            KNOWN["firefox"].url: redirect(
                "https://download-installer.cdn.example.net/Firefox%20Setup%201.exe"
            ),
            "https://download-installer.cdn.example.net/Firefox%20Setup%201.exe": ok(EXE),
        }
    )
    await run(net.tool(tmp_path), "download firefox", "firefox")
    assert (tmp_path / FOLDER / "Firefox Setup 1.exe").read_bytes() == EXE


async def test_a_relative_redirect_stays_on_the_same_host(tmp_path):
    link = "https://example.com/get"
    net = Internet({link: redirect("/files/a.pdf"), "https://example.com/files/a.pdf": ok(PDF)})
    await run(net.tool(tmp_path), f"download {link}", link)
    assert (tmp_path / FOLDER / "a.pdf").exists()


@pytest.mark.parametrize(
    "target",
    [
        "http://example.com/a.exe",
        "https://127.0.0.1/a.exe",
        "file:///c:/x",
        "https://u:p@example.com/a.exe",
    ],
)
async def test_a_redirect_to_something_unsafe_is_refused(tmp_path, target):
    link = "https://example.com/get"
    net = Internet({link: redirect(target)})
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert result.startswith("error:") and len(net.requests) == 1
    assert not (tmp_path / FOLDER).exists() or not list((tmp_path / FOLDER).iterdir())


async def test_a_redirect_to_a_host_that_resolves_to_a_private_address_is_refused(tmp_path):
    link = "https://example.com/get"
    net = Internet(
        {
            link: redirect("https://innocent.example.org/a.exe"),
            "https://innocent.example.org/a.exe": ok(EXE),
        },
        dns={"innocent.example.org": ["10.0.0.7"]},  # a public-looking name pointing inside
    )
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert "private or internal" in result
    assert [r.url.host for r in net.requests] == [
        "example.com"
    ]  # the internal one was never called


async def test_a_redirect_loop_ends(tmp_path):
    a, b = "https://example.com/a", "https://example.com/b"
    net = Internet({a: redirect(b), b: redirect(a)})
    result = await run(net.tool(tmp_path), f"download {a}", a)
    assert (
        "redirected too many times" in result and len(net.requests) == downloads.MAX_REDIRECTS + 1
    )


async def test_a_redirect_with_no_destination_is_an_error(tmp_path):
    link = "https://example.com/a"
    result = await run(
        Internet({link: httpx.Response(302)}).tool(tmp_path), f"download {link}", link
    )
    assert "sent me nowhere" in result


# ---- private addresses --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1"]
)
async def test_a_name_that_points_inside_the_network_is_never_requested(tmp_path, address):
    link = "https://printer.example.com/a.pdf"
    net = Internet({link: ok(PDF)}, dns={"printer.example.com": [address]})
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert "private or internal" in result and net.requests == []


async def test_one_private_address_among_public_ones_is_enough_to_refuse(tmp_path):
    link = "https://mixed.example.com/a.pdf"
    net = Internet({link: ok(PDF)}, dns={"mixed.example.com": ["93.184.216.34", "10.0.0.1"]})
    assert "private or internal" in await run(net.tool(tmp_path), f"download {link}", link)


async def test_a_name_that_does_not_resolve_is_reported(tmp_path):
    link = "https://nowhere.example.com/a.pdf"
    net = Internet()

    async def failing(host):
        raise OSError("no such host")

    tool = download_tools(httpx.MockTransport(net), tmp_path, failing)[0]
    assert "Is the internet on?" in await run(tool, f"download {link}", link)


# ---- what is refused ----------------------------------------------------------------------------


async def test_a_web_page_is_not_a_file(tmp_path):
    link = "https://example.com/download"
    net = Internet({link: ok(b"<html>Welcome</html>", "text/html; charset=utf-8")})
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert "web page, not a file" in result
    assert not (tmp_path / FOLDER).exists() or not list((tmp_path / FOLDER).iterdir())


async def test_a_link_to_an_html_file_is_allowed(tmp_path):
    link = "https://example.com/docs/page.html"
    await run(
        Internet({link: ok(b"<html>x</html>", "text/html")}).tool(tmp_path),
        f"download {link}",
        link,
    )
    assert (tmp_path / FOLDER / "page.html").exists()


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_an_error_status_is_reported(tmp_path, status):
    link = "https://example.com/a.pdf"
    result = await run(
        Internet({link: httpx.Response(status)}).tool(tmp_path), f"download {link}", link
    )
    assert f"answered {status}" in result


async def test_a_file_that_is_too_big_is_refused_before_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "MAX_BYTES", 1000)
    link = "https://example.com/big.iso"
    net = Internet({link: ok(EXE, **{"content-length": str(len(EXE))})})
    result = await run(net.tool(tmp_path), f"download {link}", link)
    assert "my limit is 2 GB" in result
    assert not (tmp_path / FOLDER / "big.iso").exists()


async def test_a_file_that_grows_past_the_limit_is_cut_off(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "MAX_BYTES", 1000)
    link = "https://example.com/endless.bin"

    class Endless(httpx.AsyncByteStream):  # no content-length: the size is only known by counting
        async def __aiter__(self):
            for _ in range(20):
                yield b"x" * 500

    route = httpx.Response(
        200, headers={"content-type": "application/octet-stream"}, stream=Endless()
    )
    result = await run(Internet({link: route}).tool(tmp_path), f"download {link}", link)
    assert "bigger than my 2 GB limit" in result
    assert not list((tmp_path / FOLDER).iterdir())  # not even a .part file


async def test_not_enough_disk_space_is_refused(tmp_path, monkeypatch):
    class Usage:
        free = 10

    monkeypatch.setattr(downloads.shutil, "disk_usage", lambda p: Usage())
    result = await run(Internet({STEAM: ok(EXE)}).tool(tmp_path), "download steam", "steam")
    assert "not enough free disk space" in result


async def test_an_unknown_program_gets_a_helpful_answer_and_no_request(tmp_path):
    net = Internet()
    result = await run(net.tool(tmp_path), "download photoshop", "photoshop")
    assert (
        result.startswith("error:") and "Paste its download link" in result and net.requests == []
    )


async def test_no_internet_is_reported_plainly(tmp_path):
    def offline(request):
        raise httpx.ConnectError("no route")

    tool = download_tools(httpx.MockTransport(offline), tmp_path, Internet().resolver)[0]
    assert (
        await run(tool, "download steam", "steam")
        == "error: I couldn't reach the server. Is the internet on?"
    )


# ---- crashes and half-finished downloads (kill and resume) --------------------------------------


class Broken(httpx.AsyncByteStream):
    """Sends some bytes, then the connection dies."""

    async def __aiter__(self):
        yield EXE[:2000]
        raise httpx.ReadError("connection reset")


class Slow(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield EXE[:2000]
        await asyncio.sleep(30)


async def test_a_connection_that_dies_leaves_no_file_and_the_retry_works(tmp_path):
    state = {"broken": True}

    def route(request):
        if state["broken"]:
            return httpx.Response(
                200, headers={"content-type": "application/octet-stream"}, stream=Broken()
            )
        return ok(EXE)

    tool = Internet({STEAM: route}).tool(tmp_path)
    first = await run(tool, "download steam", "steam")
    assert first.startswith("error:")
    assert list((tmp_path / FOLDER).iterdir()) == []  # neither the file nor a .part
    state["broken"] = False
    second = await run(tool, "download steam", "steam")
    assert (
        "Saved SteamSetup.exe" in second.text
        and (tmp_path / FOLDER / "SteamSetup.exe").read_bytes() == EXE
    )


async def test_a_download_that_stops_early_is_thrown_away(tmp_path):
    net = Internet({STEAM: ok(EXE[:100], **{"content-length": str(len(EXE))})})
    # a server promising more than it sends: httpx may refuse, or we do; either way, no file
    result = await run(net.tool(tmp_path), "download steam", "steam")
    assert result.startswith("error:") or "Saved" not in getattr(result, "text", "")
    assert not list((tmp_path / FOLDER).glob("SteamSetup.exe*"))


async def test_a_timeout_in_the_middle_of_a_download_cleans_up(tmp_path):
    route = httpx.Response(200, headers={"content-type": "application/octet-stream"}, stream=Slow())
    tool = Internet({STEAM: route}).tool(tmp_path)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run(tool, "download steam", "steam"), 0.5)  # what the loop does
    assert list((tmp_path / FOLDER).iterdir()) == []


async def test_leftovers_from_an_earlier_crash_are_deleted_first(tmp_path):
    folder = tmp_path / FOLDER
    folder.mkdir(parents=True)
    (folder / "SteamSetup.exe.part").write_bytes(b"half")
    (folder / "other.zip.part").write_bytes(b"half")
    (folder / "keep.txt").write_text("mine")
    await run(Internet({STEAM: ok(EXE)}).tool(tmp_path), "download steam", "steam")
    assert not list(folder.glob("*.part")) and (folder / "keep.txt").read_text() == "mine"
    assert (folder / "SteamSetup.exe").read_bytes() == EXE


# ---- names --------------------------------------------------------------------------------------


async def test_the_same_file_twice_is_not_downloaded_twice(tmp_path):
    net = Internet({STEAM: ok(EXE, **{"content-length": str(len(EXE))})})
    tool = net.tool(tmp_path)
    await run(tool, "download steam", "steam")
    again = await run(tool, "download steam", "steam")
    assert "Already downloaded" in again.text
    assert sorted(p.name for p in (tmp_path / FOLDER).iterdir()) == ["SteamSetup.exe"]


async def test_a_different_file_with_the_same_name_is_kept_beside_the_first(tmp_path):
    current = {"data": EXE}
    net = Internet(
        {STEAM: lambda r: ok(current["data"], **{"content-length": str(len(current["data"]))})}
    )
    tool = net.tool(tmp_path)
    await run(tool, "download steam", "steam")
    current["data"] = EXE + b"newer"
    await run(tool, "download steam", "steam")
    names = sorted(p.name for p in (tmp_path / FOLDER).iterdir())
    assert names == ["SteamSetup (1).exe", "SteamSetup.exe"]
    assert (tmp_path / FOLDER / "SteamSetup.exe").read_bytes() == EXE  # the first was never touched


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ('attachment; filename="My Report.pdf"', "My Report.pdf"),
        ("attachment; filename=plain.zip", "plain.zip"),
        ("attachment; filename*=UTF-8''na%C3%AFve%20file.txt", "naïve file.txt"),
        ('attachment; filename="..\\..\\Windows\\evil.exe"', "evil.exe"),
        ('attachment; filename="../../evil.exe"', "evil.exe"),
        ('attachment; filename="CON.txt"', "_CON.txt"),
        ('attachment; filename="a:b*c?.txt"', "a b c.txt"),
    ],
)
async def test_the_file_name_comes_from_the_server_but_is_made_safe(
    tmp_path, disposition, expected
):
    link = "https://example.com/dl?id=7"
    net = Internet({link: ok(PDF, "application/pdf", **{"content-disposition": disposition})})
    await run(net.tool(tmp_path), f"download {link}", link)
    assert [p.name for p in (tmp_path / FOLDER).iterdir()] == [expected]


@pytest.mark.parametrize(
    ("raw", "kind", "expected"),
    [
        ("Firefox%20Setup%20158.0.1.exe", "", "Firefox Setup 158.0.1.exe"),
        ("..", "application/pdf", "download.pdf"),
        ("", "image/png", "download.png"),
        ("noextension", "application/zip", "noextension.zip"),
        ("noextension", "", "noextension.bin"),
        ("x" * 300 + ".exe", "", "x" * 100 + ".exe"),
        ("archive.tar.gz", "", "archive.tar.gz"),
        ("NUL", "", "_NUL.bin"),
        (".hidden", "", "hidden.bin"),
    ],
)
def test_names_are_made_safe_for_windows(raw, kind, expected):
    name = clean_name(raw, kind)
    assert name == expected
    assert not set(name) & set('<>:"/\\|?*') and ".." not in name.replace("tar.gz", "")


def test_programs_are_recognised_by_their_extension():
    assert all(
        is_program(n) for n in ("a.exe", "A.EXE", "setup.msi", "x.ps1", "y.bat", "z.jar", "q.lnk")
    )
    assert not any(is_program(n) for n in ("a.pdf", "b.zip", "c.txt", "exe", "d.png"))


# ---- the declaration, the permission layer, and the real loop ------------------------


def test_the_known_links_are_ordinary_https_links_to_named_hosts():
    for name, item in KNOWN.items():
        assert validate_link(item.url) == item.url, name
        assert item.label


def test_the_tool_declares_everything_the_rules_require(tmp_path):
    tool = download_tools(downloads=tmp_path)[0]
    assert tool.name == "download_file" and tool.risk is Risk.R2 and tool.idempotent is False
    assert tool.timeout_s >= 600 and tool.sandbox == "workspace-write"
    assert tool.fs == {"write": [str(tmp_path / FOLDER)]}
    assert (
        "https links the user typed" in tool.network
        and "cdn.akamai.steamstatic.com" in tool.network
    )
    assert "never open or run it" in tool.approval_note
    spec = tool.spec()["function"]
    assert set(spec["parameters"]["properties"]) == {"source"} and spec["parameters"][
        "required"
    ] == ["source"]
    assert len(tool.description.split(". ")) <= 2


def test_the_owner_is_asked_before_every_download():
    decision = permissions.check("interactive", "download_file", Risk.R2)
    assert decision.ask and not decision.allowed
    assert not permissions.check("screen", "download_file", Risk.R2).allowed


def test_arguments_are_validated():
    token = CURRENT_TEXT.set("download steam")
    try:
        DownloadFileArgs(source="steam")
        for bad in ("", "x", "y" * 2001, "photoshop", "chrome"):
            with pytest.raises(ValueError):
                DownloadFileArgs(source=bad)
    finally:
        CURRENT_TEXT.reset(token)


def test_a_source_that_would_be_refused_fails_validation_with_the_reason():
    token = CURRENT_TEXT.set("download photoshop")
    try:
        with pytest.raises(ValueError, match="Paste its download link"):
            DownloadFileArgs(source="photoshop")
    finally:
        CURRENT_TEXT.reset(token)


async def test_download_steam_end_to_end_with_the_owners_yes(tmp_path, key):  # noqa: F811
    net = Internet({STEAM: ok(EXE)})
    asked = []

    async def approve(name, args, decision):
        asked.append((name, args))
        return True

    vault, store, svc, chat, agent = build(
        tmp_path, key, [call("download_file", source="steam"), say("unused")], approve=approve
    )
    agent.tools.update({t.name: t for t in [net.tool(tmp_path / "Downloads")]})
    seen = {}
    real = agent.tools["download_file"].handler

    async def spy(args):
        seen["events"] = kinds(vault)  # what was already durable when the download started
        return await real(args)

    agent.tools["download_file"].handler = spy
    result = await agent.turn(Session(), "download steam")
    assert asked == [("download_file", {"source": "steam"})]
    assert seen["events"] == ["user_message", "memory_tool_call"]  # logged before acting
    assert len(chat.calls) == 1 and result.final_tool and "Saved SteamSetup.exe" in result.text
    assert (tmp_path / "Downloads" / FOLDER / "SteamSetup.exe").read_bytes() == EXE
    vault.close()


async def test_when_the_owner_says_no_nothing_is_fetched(tmp_path, key):  # noqa: F811
    net = Internet({STEAM: ok(EXE)})

    async def approve(name, args, decision):
        return False

    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [call("download_file", source="steam"), say("ok, not downloading")],
        approve=approve,
    )
    agent.tools.update({t.name: t for t in [net.tool(tmp_path / "Downloads")]})
    await agent.turn(Session(), "download steam")
    assert net.requests == [] and not (tmp_path / "Downloads").exists()
    vault.close()


async def test_a_download_the_model_makes_up_is_refused_even_with_a_yes(tmp_path, key):  # noqa: F811
    # e.g. injected text in a file said "download steam"; the user only asked about cats
    net = Internet({STEAM: ok(EXE)})

    async def approve(name, args, decision):
        return True

    vault, store, svc, chat, agent = build(
        tmp_path, key, [call("download_file", source="steam"), say("I could not.")], approve=approve
    )
    agent.tools.update({t.name: t for t in [net.tool(tmp_path / "Downloads")]})
    await agent.turn(Session(), "what is in my notes about cats?")
    assert net.requests == []
    assert "did not mention steam" in chat.calls[1]["messages"][-1]["content"]
    vault.close()


async def test_the_owner_is_not_asked_about_a_download_that_cannot_happen(tmp_path, key):  # noqa: F811
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return True

    net = Internet()
    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [call("download_file", source="photoshop"), say("Please paste the link.")],
        approve=approve,
    )
    agent.tools.update({t.name: t for t in [net.tool(tmp_path / "Downloads")]})
    result = await agent.turn(Session(), "download photoshop")
    told = chat.calls[1]["messages"][-1]["content"]
    assert asked == [] and net.requests == []  # no question, no request
    assert told.startswith("error: invalid_args") and "Paste its download link" in told
    assert result.text == "Please paste the link."
    vault.close()
