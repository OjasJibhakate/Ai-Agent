"""The picture download skill: what may be asked for, what may be fetched, what may be kept.

No test here touches the internet: a fake server (httpx.MockTransport) plays Wikimedia Commons.
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.runtime.tools import CURRENT_TEXT, PROGRESS
from ragnarok.skills import images
from ragnarok.skills.images import (
    DownloadImagesArgs,
    ImageError,
    check_query,
    has_image_signature,
    image_tools,
    parse_candidates,
    safe_filename,
    safe_url,
)
from tests.test_m2_memory import key  # noqa: F401  (fixture)
from tests.test_m4_agent import build, call, kinds, say

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 200
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"w" * 50
GIF = b"GIF89a" + b"g" * 50


def page(i, title, mime="image/jpeg", host="upload.wikimedia.org", **extra):
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(
        mime, "svg"
    )
    info = {
        "thumburl": f"https://{host}/thumb/{i}.{ext}",
        "url": f"https://{host}/orig/{i}.{ext}",
        "mime": mime,
        "size": 5000,
        "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
        "extmetadata": {
            "Artist": {"value": f'<a href="//x">Alice {i}</a> &amp; Bob'},
            "LicenseShortName": {"value": "CC BY-SA 4.0"},
        },
    }
    info.update(extra)
    return {"pageid": i, "title": title, "index": i, "imageinfo": [info]}


class FakeCommons:
    """Plays commons.wikimedia.org (search) and upload.wikimedia.org (files)."""

    def __init__(self, pages, files=None, status=200):
        self.pages = pages
        self.files = files or {}  # url -> (content type, bytes) or an httpx.Response status int
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "commons.wikimedia.org":
            if self.status != 200:
                return httpx.Response(self.status)
            return httpx.Response(200, json={"query": {"pages": self.pages}})
        served = self.files.get(str(request.url))
        if served is None:
            return httpx.Response(404)
        if isinstance(served, int):
            return httpx.Response(served, headers={"location": "https://evil.example/x.jpg"})
        kind, data = served
        return httpx.Response(200, headers={"content-type": kind}, content=data)

    def tool(self, tmp_path):
        return image_tools(httpx.MockTransport(self), downloads=tmp_path)[0]


async def run(tool, user_text, **args):
    token = CURRENT_TEXT.set(user_text)
    try:
        return await tool.handler(DownloadImagesArgs(**args))
    finally:
        CURRENT_TEXT.reset(token)


def text_of(result):
    return getattr(result, "text", result)


def three_cats():
    pages = [page(i, f"File:Cat {i}.jpg") for i in (1, 2, 3)]
    files = {f"https://upload.wikimedia.org/thumb/{i}.jpg": ("image/jpeg", JPEG) for i in (1, 2, 3)}
    return FakeCommons(pages, files)


# ---- the happy path ----------


async def test_download_cat_images_saves_pictures_and_credits(tmp_path):
    server = three_cats()
    result = await run(server.tool(tmp_path), "download cat images", query="cat", count=3)
    folder = tmp_path / "Ragnarok" / "cat"
    assert sorted(p.name for p in folder.glob("*.jpg")) == ["Cat_1.jpg", "Cat_2.jpg", "Cat_3.jpg"]
    assert all(p.read_bytes() == JPEG for p in folder.glob("*.jpg"))
    assert (
        result.final and "Saved 3 pictures of 'cat'" in result.text and str(folder) in result.text
    )
    credits = (folder / "credits.txt").read_text(encoding="utf-8").splitlines()
    assert len(credits) == 3
    assert credits[0] == (
        "Cat_1.jpg | Alice 1 & Bob | CC BY-SA 4.0 | https://commons.wikimedia.org/wiki/File:Cat_1.jpg"
    )  # markup stripped, entities decoded


async def test_the_search_asks_commons_for_pictures_only(tmp_path):
    server = three_cats()
    await run(server.tool(tmp_path), "download cat images", query="cat", count=2)
    search = server.requests[0]
    q = parse_qs(urlsplit(str(search.url)).query)
    assert q["gsrsearch"] == ["cat filetype:bitmap"] and q["gsrnamespace"] == ["6"]
    assert q["gsrlimit"] == ["6"]  # a few spares for ones that get filtered out
    assert search.headers["user-agent"].startswith("Ragnarok/")
    assert {r.url.host for r in server.requests} <= {
        "commons.wikimedia.org",
        "upload.wikimedia.org",
    }


async def test_default_is_five_and_the_tool_stops_when_it_has_enough(tmp_path):
    pages = [page(i, f"File:Cat {i}.jpg") for i in range(1, 9)]
    files = {
        f"https://upload.wikimedia.org/thumb/{i}.jpg": ("image/jpeg", JPEG) for i in range(1, 9)
    }
    server = FakeCommons(pages, files)
    result = await run(server.tool(tmp_path), "download cat images", query="cat")
    assert len(list((tmp_path / "Ragnarok" / "cat").glob("*.jpg"))) == 5
    assert "Saved 5 pictures" in result.text
    assert len([r for r in server.requests if r.url.host == "upload.wikimedia.org"]) == 5


async def test_a_request_for_more_than_ten_is_cut_to_ten_and_says_so(tmp_path):
    pages = [page(i, f"File:Cat {i}.jpg") for i in range(1, 20)]
    files = {
        f"https://upload.wikimedia.org/thumb/{i}.jpg": ("image/jpeg", JPEG) for i in range(1, 20)
    }
    result = await run(
        FakeCommons(pages, files).tool(tmp_path), "download 40 cat images", query="cat", count=40
    )
    assert "Saved 10 pictures" in result.text and "at most 10" in result.text


async def test_fewer_found_than_wanted_is_reported_plainly(tmp_path):
    result = await run(three_cats().tool(tmp_path), "download cat images", query="cat", count=8)
    assert "Saved 3 pictures" in result.text and "3 of 8" in result.text


async def test_nothing_found_says_so(tmp_path):
    result = await run(FakeCommons([]).tool(tmp_path), "download unicorn images", query="unicorn")
    assert "no pictures of 'unicorn'" in result.text
    assert not list(tmp_path.rglob("*.jpg"))


async def test_progress_lines_are_shown(tmp_path):
    seen = []

    async def progress(line):
        seen.append(line)

    token = PROGRESS.set(progress)
    try:
        await run(three_cats().tool(tmp_path), "download cat images", query="cat", count=2)
    finally:
        PROGRESS.reset(token)
    assert seen == ["downloading 1 of 2: Cat_1.jpg", "downloading 2 of 2: Cat_2.jpg"]


# ---- asking twice, and crashing half-way ----------


async def test_asking_again_does_not_download_again(tmp_path):
    server = three_cats()
    tool = server.tool(tmp_path)
    await run(tool, "download cat images", query="cat", count=3)
    before = len([r for r in server.requests if r.url.host == "upload.wikimedia.org"])
    again = await run(tool, "download cat images", query="cat", count=3)
    after = len([r for r in server.requests if r.url.host == "upload.wikimedia.org"])
    assert after == before  # nothing fetched the second time
    assert "Saved 0 pictures" in again.text and "3 were already there" in again.text
    credits = (tmp_path / "Ragnarok" / "cat" / "credits.txt").read_text(encoding="utf-8")
    assert len(credits.splitlines()) == 3  # no duplicate credit lines


async def test_an_existing_file_is_never_overwritten(tmp_path):
    folder = tmp_path / "Ragnarok" / "cat"
    folder.mkdir(parents=True)
    (folder / "Cat_1.jpg").write_bytes(b"my own file")
    await run(three_cats().tool(tmp_path), "download cat images", query="cat", count=3)
    assert (folder / "Cat_1.jpg").read_bytes() == b"my own file"


async def test_a_crash_half_way_is_cleaned_up_and_resumed(tmp_path):
    # kill-and-resume: a first run died while writing, leaving a .part file and one finished picture
    folder = tmp_path / "Ragnarok" / "cat"
    folder.mkdir(parents=True)
    (folder / "Cat_1.jpg").write_bytes(JPEG)
    (folder / "Cat_2.jpg.part").write_bytes(b"\xff\xd8half")
    result = await run(three_cats().tool(tmp_path), "download cat images", query="cat", count=3)
    assert not list(folder.glob("*.part"))
    assert (folder / "Cat_2.jpg").read_bytes() == JPEG
    assert "Saved 2 pictures" in result.text and "1 were already there" in result.text


async def test_a_download_that_fails_leaves_no_partial_file(tmp_path, monkeypatch):
    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(images, "_save", boom)
    result = await run(three_cats().tool(tmp_path), "download cat images", query="cat")
    assert result.startswith("error:") and "couldn't save" in result
    assert not list((tmp_path / "Ragnarok").rglob("*.jpg*"))


# ---- what may be asked for ----------


async def test_search_words_that_the_user_never_wrote_are_refused(tmp_path):
    # e.g. text from a file the model read earlier, smuggled in as a "search"
    server = three_cats()
    result = await run(
        server.tool(tmp_path), "download cat images", query="my bank password hunter2"
    )
    assert result.startswith("error:") and "words you wrote yourself" in result
    assert server.requests == []  # nothing was sent anywhere


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("cat", "download cat images"),
        ("cats", "download cat images"),  # plural either way
        ("cat", "get me pictures of cats"),
        ("Golden Retriever", "download golden retriever photos"),
        ("red panda", "download images of a red panda"),
    ],
)
def test_words_the_user_used_are_accepted(query, message):
    assert check_query(query, message)


@pytest.mark.parametrize(
    "query",
    ["", "  ", "cat; rm -rf", "cat/../../x", "cat " * 30, "https://evil.example"],
)
def test_unusable_searches_are_refused(query):
    with pytest.raises(ImageError):
        check_query(query, "download cat images " + query)


# ---- what may be fetched ----------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://upload.wikimedia.org/a/b.jpg", True),
        ("https://commons.wikimedia.org/w/index.php", True),
        (
            "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b6/Cat.jpg/1280px-Cat.jpg?utm_source=commons.wikimedia.org",
            True,
        ),
        ("https://thumb.wikimedia.org.evil.example/a.jpg", False),
        ("https://evilthumb.wikimedia.org/a.jpg", False),
        ("http://upload.wikimedia.org/a.jpg", False),
        ("https://evil.example/a.jpg", False),
        ("https://upload.wikimedia.org.evil.example/a.jpg", False),
        ("https://user:pw@upload.wikimedia.org/a.jpg", False),
        ("https://upload.wikimedia.org:8443/a.jpg", False),
        ("https://127.0.0.1/a.jpg", False),
        ("file:///c:/secret.txt", False),
        ("//upload.wikimedia.org/a.jpg", False),
        ("", False),
    ],
)
def test_only_the_three_wikimedia_hosts_over_https(url, ok):
    assert safe_url(url) is ok


async def test_a_picture_on_another_host_is_never_requested(tmp_path):
    pages = [page(1, "File:Cat 1.jpg", host="evil.example"), page(2, "File:Cat 2.jpg")]
    files = {"https://upload.wikimedia.org/thumb/2.jpg": ("image/jpeg", JPEG)}
    server = FakeCommons(pages, files)
    result = await run(server.tool(tmp_path), "download cat images", query="cat", count=2)
    assert "evil.example" not in {r.url.host for r in server.requests}
    assert "Saved 1 picture " in result.text


async def test_a_redirect_is_not_followed(tmp_path):
    pages = [page(1, "File:Cat 1.jpg"), page(2, "File:Cat 2.jpg")]
    files = {
        "https://upload.wikimedia.org/thumb/1.jpg": 302,  # tries to send us to evil.example
        "https://upload.wikimedia.org/thumb/2.jpg": ("image/jpeg", JPEG),
    }
    server = FakeCommons(pages, files)
    result = await run(server.tool(tmp_path), "download cat images", query="cat", count=2)
    assert "evil.example" not in {r.url.host for r in server.requests}
    assert [p.name for p in (tmp_path / "Ragnarok" / "cat").glob("*.jpg")] == ["Cat_2.jpg"]
    assert "Saved 1 picture " in result.text


# ---- what may be kept ----------


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("text/html", b"<html>not a picture</html>"),
        ("image/svg+xml", b"<svg onload=alert(1)/>"),
        ("application/octet-stream", JPEG),
        ("image/jpeg", b"MZ\x90\x00 this is really an executable"),  # wrong first bytes
        ("image/png", JPEG),  # claims png, is jpeg
        ("image/jpeg", b""),
    ],
)
async def test_things_that_are_not_the_promised_picture_are_dropped(tmp_path, kind, data):
    pages = [page(1, "File:Cat 1.jpg"), page(2, "File:Cat 2.jpg")]
    files = {
        "https://upload.wikimedia.org/thumb/1.jpg": (kind, data),
        "https://upload.wikimedia.org/thumb/2.jpg": ("image/jpeg", JPEG),
    }
    result = await run(
        FakeCommons(pages, files).tool(tmp_path), "download cat images", query="cat", count=2
    )
    assert [p.name for p in (tmp_path / "Ragnarok" / "cat").glob("*.jpg")] == ["Cat_2.jpg"]
    assert "Saved 1 picture " in result.text


async def test_a_file_over_the_size_limit_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_FILE_BYTES", 100)
    pages = [page(1, "File:Cat 1.jpg"), page(2, "File:Cat 2.jpg")]
    files = {
        "https://upload.wikimedia.org/thumb/1.jpg": ("image/jpeg", JPEG),  # 204 bytes
        "https://upload.wikimedia.org/thumb/2.jpg": ("image/jpeg", b"\xff\xd8\xff" + b"s" * 50),
    }
    result = await run(
        FakeCommons(pages, files).tool(tmp_path), "download cat images", query="cat", count=2
    )
    assert [p.name for p in (tmp_path / "Ragnarok" / "cat").glob("*.jpg")] == ["Cat_2.jpg"]
    assert "Saved 1 picture " in result.text


async def test_the_total_size_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_TOTAL_BYTES", 450)  # room for two 204-byte pictures
    result = await run(three_cats().tool(tmp_path), "download cat images", query="cat", count=3)
    assert "Saved 2 pictures" in result.text


async def test_each_picture_type_is_recognised_by_its_first_bytes():
    assert has_image_signature(JPEG, "image/jpeg") and has_image_signature(PNG, "image/png")
    assert has_image_signature(WEBP, "image/webp") and has_image_signature(GIF, "image/gif")
    assert not has_image_signature(PNG, "image/jpeg") and not has_image_signature(b"", "image/gif")
    assert not has_image_signature(JPEG, "image/svg+xml")


async def test_svg_and_other_non_bitmap_results_are_not_candidates():
    payload = {
        "query": {
            "pages": [
                page(1, "File:Logo.svg", mime="image/svg+xml"),
                page(2, "File:Doc.pdf", mime="application/pdf"),
                page(3, "File:Cat.png", mime="image/png"),
            ]
        }
    }
    assert [c.title for c in parse_candidates(payload)] == ["File:Cat.png"]


def test_results_come_back_in_search_rank_order():
    payload = {
        "query": {"pages": [page(3, "File:C.jpg"), page(1, "File:A.jpg"), page(2, "File:B.jpg")]}
    }
    assert [c.title for c in parse_candidates(payload)] == [
        "File:A.jpg",
        "File:B.jpg",
        "File:C.jpg",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": {}},
        {"query": {"pages": [{"title": "File:x.jpg"}]}},
        {"query": {"pages": [{"title": "File:x.jpg", "imageinfo": [{}]}]}},
    ],
)
def test_odd_replies_give_no_candidates_instead_of_crashing(payload):
    assert parse_candidates(payload) == []


# ---- file names ----------


@pytest.mark.parametrize(
    ("title", "mime", "expected"),
    [
        ("File:Cat.jpg", "image/jpeg", "Cat.jpg"),
        ("File:A cat on a sofa.JPG", "image/jpeg", "A_cat_on_a_sofa.jpg"),
        ("File:Odd/<name>:with|bad?chars*.png", "image/png", "Odd_name_with_bad_chars.png"),
        ("File:..\\..\\evil.jpg", "image/jpeg", "evil.jpg"),
        ("File:CON.jpg", "image/jpeg", "_CON.jpg"),
        ("File:nul.png", "image/png", "_nul.png"),
        ("File:...jpg", "image/jpeg", "picture.jpg"),
        ("File:" + "x" * 300 + ".jpg", "image/jpeg", "x" * 80 + ".jpg"),
        ("File:Cat.svg", "image/webp", "Cat.webp"),  # the extension follows the real type
    ],
)
def test_file_names_are_safe_on_windows(title, mime, expected):
    name = safe_filename(title, mime)
    assert name == expected
    assert "/" not in name and "\\" not in name and ":" not in name and ".." not in name


# ---- when things go wrong ----------


async def test_no_internet_is_reported_plainly(tmp_path):
    def offline(request):
        raise httpx.ConnectError("no route")

    tool = image_tools(httpx.MockTransport(offline), downloads=tmp_path)[0]
    result = await run(tool, "download cat images", query="cat")
    assert result == "error: I couldn't reach Wikimedia Commons. Is the internet on?"


async def test_a_timeout_is_reported_plainly(tmp_path):
    def slow(request):
        raise httpx.ReadTimeout("too slow")

    tool = image_tools(httpx.MockTransport(slow), downloads=tmp_path)[0]
    assert "couldn't reach" in await run(tool, "download cat images", query="cat")


async def test_a_server_error_is_reported(tmp_path):
    result = await run(
        FakeCommons([], status=503).tool(tmp_path), "download cat images", query="cat"
    )
    assert result == "error: Wikimedia Commons answered 503"


async def test_garbage_from_the_server_is_reported_not_raised(tmp_path):
    def garbage(request):
        return httpx.Response(200, content=b"<html>captive portal</html>")

    tool = image_tools(httpx.MockTransport(garbage), downloads=tmp_path)[0]
    assert "could not read" in await run(tool, "download cat images", query="cat")


# ---- the tool's declaration and the permission layer (AGENTS.md hard rules 3 and 4) ----------


def test_the_tool_declares_everything_the_rules_require(tmp_path):
    tool = image_tools(downloads=tmp_path)[0]
    assert tool.name == "download_images"
    assert tool.risk is Risk.R1 and tool.idempotent is True and tool.timeout_s > 0
    assert tool.sandbox == "workspace-write"
    assert tool.network == [
        "commons.wikimedia.org",
        "thumb.wikimedia.org",
        "upload.wikimedia.org",
    ]
    assert tool.fs == {"write": [str(tmp_path / "Ragnarok")]}
    spec = tool.spec()["function"]
    assert spec["name"] == "download_images" and set(spec["parameters"]["properties"]) == {
        "query",
        "count",
    }
    assert spec["parameters"]["required"] == ["query"]
    assert len(tool.description.split(". ")) <= 2  # at most two concrete sentences


def test_arguments_are_validated():
    DownloadImagesArgs(query="cat")
    for bad in (
        {"query": "x"},
        {"query": "cat", "count": 0},
        {"query": "cat", "count": 101},
        {"query": "y" * 81},
    ):
        with pytest.raises(ValueError):
            DownloadImagesArgs(**bad)


def test_permissions_the_owner_may_run_it_but_triggers_and_screen_tasks_may_not():
    assert permissions.check("interactive", "download_images", Risk.R1).allowed
    background = permissions.check("background", "download_images", Risk.R1)
    assert not background.allowed and background.ask  # unattended runs wait for a yes
    assert not permissions.check("screen", "download_images", Risk.R1).allowed


# ---- through the real agent loop ----------


async def test_download_cat_images_end_to_end_through_the_agent(tmp_path, key):  # noqa: F811
    downloads = tmp_path / "Downloads"
    server = three_cats()
    vault, store, svc, chat, agent = build(
        tmp_path, key, [call("download_images", query="cat", count=3), say("unused")]
    )
    agent.tools.update({t.name: t for t in image_tools(httpx.MockTransport(server), downloads)})
    seen = {}
    real = agent.tools["download_images"].handler

    async def spy(args):
        seen["events"] = kinds(vault)  # what was already durable when the download ran
        return await real(args)

    agent.tools["download_images"].handler = spy
    result = await agent.turn(Session(), "download cat images")
    assert seen["events"] == ["user_message", "memory_tool_call"]  # logged before acting
    assert (
        len(chat.calls) == 1 and result.final_tool
    )  # one model call: the tool's sentence is the answer
    assert "Saved 3 pictures of 'cat'" in result.text
    assert len(list((downloads / "Ragnarok" / "cat").glob("*.jpg"))) == 3
    vault.close()


async def test_a_search_smuggled_in_by_the_model_is_refused_and_the_model_is_told(tmp_path, key):  # noqa: F811
    server = three_cats()
    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [call("download_images", query="secret api key from my notes"), say("I can't do that.")],
    )
    agent.tools.update(
        {t.name: t for t in image_tools(httpx.MockTransport(server), tmp_path / "Downloads")}
    )
    await agent.turn(Session(), "download cat images")
    assert server.requests == []  # not one byte left the machine
    told = chat.calls[1]["messages"][-1]["content"]
    assert told.startswith("error:") and "words you wrote yourself" in told
    vault.close()


async def test_a_background_run_must_ask_before_downloading(tmp_path, key):  # noqa: F811
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return False  # the owner says no

    server = three_cats()
    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [call("download_images", query="cat"), say("ok, not downloading")],
        profile="background",
        approve=approve,
    )
    agent.tools.update(
        {t.name: t for t in image_tools(httpx.MockTransport(server), tmp_path / "Downloads")}
    )
    await agent.turn(Session(), "download cat images")
    assert asked == ["download_images"] and server.requests == []
    assert not (tmp_path / "Downloads").exists()
    vault.close()


def test_building_the_tool_writes_nothing_to_disk(tmp_path):
    image_tools(downloads=tmp_path)
    safe_filename("File:x.jpg", "image/jpeg")
    assert list(tmp_path.iterdir()) == []


def test_whitespace_is_squeezed_so_only_plain_words_are_ever_sent():
    squeezed = check_query("  Golden \n\t Retriever ", "download golden retriever photos")
    assert squeezed == "golden retriever"


async def test_the_real_shape_of_a_commons_reply_thumbnails_on_thumb_wikimedia_org(tmp_path):
    # what the live API sent when this was first run for real (the host moved off upload.*)
    url = (
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b6/Cat.jpg/1280px-Cat.jpg"
        "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail"
    )
    pages = [page(1, "File:Felis catus-cat on snow.jpg", thumburl=url)]
    server = FakeCommons(pages, {url: ("image/jpeg", JPEG)})
    result = await run(server.tool(tmp_path), "download cat images", query="cat", count=1)
    assert "Saved 1 picture " in result.text
    assert (tmp_path / "Ragnarok" / "cat" / "Felis_catus-cat_on_snow.jpg").read_bytes() == JPEG
