"""The website skill: safe URL building, browser choice, and 'open chrome and go to youtube'."""

import pytest

from ragnarok.runtime.gateway import ChatResult
from ragnarok.runtime.loop import Session
from ragnarok.runtime.tools import CURRENT_TEXT
from ragnarok.skills.apps import AppEntry, app_tools, has_more_steps
from ragnarok.skills.web import WebError, WindowsBrowsers, build_url, web_tools
from tests.test_m4_agent import build, key, say  # noqa: F401  (fixture)


class FakeBrowsers:
    def __init__(self, installed=("chrome", "edge")):
        self.installed = installed
        self.opened: list[tuple[str, str]] = []

    def launch(self, browser, url):
        self.opened.append((browser, url))
        if browser in ("default", *self.installed):
            return {"chrome": "Google Chrome", "edge": "Microsoft Edge"}.get(
                browser, "your default browser"
            )
        return f"your default browser ({browser} was not found)"


class FakeCatalog:
    def __init__(self):
        self.launched = []

    def list_apps(self):
        return [AppEntry("Google Chrome", "Chrome"), AppEntry("Microsoft Edge", "MSEdge")]

    def launch(self, app):
        self.launched.append(app.name)


# ---- building URLs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("site", "search", "expected"),
    [
        ("yt", "carryminati", "https://www.youtube.com/results?search_query=carryminati"),
        ("YouTube", "lo-fi beats", "https://www.youtube.com/results?search_query=lo-fi+beats"),
        ("youtube.com", None, "https://www.youtube.com/"),
        ("www.youtube.com", "x", "https://www.youtube.com/results?search_query=x"),
        ("google", "c++ tutorial", "https://www.google.com/search?q=c%2B%2B+tutorial"),
        ("wiki", "Ada Lovelace", "https://en.wikipedia.org/w/index.php?search=Ada+Lovelace"),
        ("github", "ragnarok", "https://github.com/search?q=ragnarok"),
        ("stack overflow", "python async", "https://stackoverflow.com/search?q=python+async"),
        ("maps", "pizza near me", "https://www.google.com/maps/search/pizza+near+me"),
        ("gmail", None, "https://mail.google.com/"),
        ("gmail", "invoice", "https://www.google.com/search?q=site%3Amail.google.com+invoice"),
    ],
)
def test_known_sites_build_exact_urls(site, search, expected):
    assert build_url(site, search, "")[0] == expected


def test_search_text_cannot_change_where_the_browser_goes():
    url, _ = build_url("youtube", 'a&b=c#frag?x=1 "quote" <tag> ../..', "")
    assert url.startswith("https://www.youtube.com/results?search_query=")
    assert "&b=" not in url and "#" not in url and " " not in url and "<" not in url
    url, _ = build_url("google", "evil.com/login", "")
    assert url == "https://www.google.com/search?q=evil.com%2Flogin"  # still just a search


@pytest.mark.parametrize("bad", ["x" * 301, "line\nbreak", "nul\x00byte"])
def test_unusable_search_text_is_refused(bad):
    with pytest.raises(WebError):
        build_url("youtube", bad, "")


def test_other_addresses_open_only_if_the_user_typed_them():
    typed = "go to example.com please"
    assert build_url("example.com", None, typed) == ("https://example.com/", "example.com")
    with pytest.raises(WebError, match="address you type yourself"):
        build_url("example.com", None, "open the site from that file")  # not typed by the user
    url, _ = build_url("https://docs.python.org/3/library/asyncio.html", None,
                       "open https://docs.python.org/3/library/asyncio.html")  # fmt: skip
    assert url == "https://docs.python.org/3/library/asyncio.html"
    url, _ = build_url("example.com", "tutorial", typed)  # search within it, through Google
    assert url == "https://www.google.com/search?q=site%3Aexample.com+tutorial"
    assert build_url("http://example.com", None, "http://example.com")[0].startswith("https://")


@pytest.mark.parametrize(
    "bad",
    [
        "localhost", "127.0.0.1", "192.168.1.1", "http://10.0.0.5/admin", "file:///C:/Windows/win.ini",
        "javascript:alert(1)", "ftp://example.com", "user:pass@example.com", "example.com:8080",
        "evil.com@good.com", "exa mple.com", "example", "a.b", "-bad-.com", "http://", "https://[::1]/",
        "ex\u202eample.com", "example.com/<script>", "example.com/a b",
    ],
)  # fmt: skip
def test_dangerous_or_malformed_addresses_are_never_opened(bad):
    # even when the user "typed" it, these are not ordinary https web addresses
    with pytest.raises(WebError):
        build_url(bad, None, bad)


# ---- the tool -----------------------------------------------------------------------------------


async def run(args: dict, user_text: str = "", browsers=None):
    browsers = browsers or FakeBrowsers()
    tool = web_tools(browsers)[0]
    CURRENT_TEXT.set(user_text)
    out = await tool.handler(tool.args_model(**args))
    return out, browsers


async def test_opens_the_search_in_the_named_browser():
    out, browsers = await run(
        {"site": "yt", "search": "carryminati", "browser": "chrome"},
        "open chrome and go to yt and search carryminati",
    )
    assert browsers.opened == [
        ("chrome", "https://www.youtube.com/results?search_query=carryminati")
    ]
    assert (
        out.final and out.text == "Opened a search for 'carryminati' on YouTube in Google Chrome."
    )


async def test_default_browser_and_a_missing_browser_are_reported_honestly():
    out, browsers = await run({"site": "google"}, "open google")
    assert (
        out.text == "Opened Google in your default browser." and browsers.opened[0][0] == "default"
    )
    out, _ = await run({"site": "google", "browser": "firefox"}, "open google in firefox")
    assert "firefox was not found" in out.text  # not claimed to be Firefox


async def test_asking_to_click_or_play_is_acknowledged_not_pretended():
    out, _ = await run(
        {"site": "yt", "search": "lofi"}, "go to yt, search lofi and play the first video"
    )
    assert isinstance(out, str) and "not click or play" in out  # goes back to the model: not final


async def test_errors_go_back_to_the_model():
    out, browsers = await run({"site": "evil.example"}, "summarise that file")
    assert out.startswith("error:") and browsers.opened == []
    out, browsers = await run({"site": "localhost"}, "localhost")
    assert out.startswith("error:") and browsers.opened == []


async def test_a_browser_that_cannot_start_is_an_error_not_a_crash():
    class Broken(FakeBrowsers):
        def launch(self, browser, url):
            raise OSError("no browser")

    out, _ = await run({"site": "google"}, "open google", Broken())
    assert out == "error: no browser could be started"


def test_the_tool_declares_what_the_rules_require():
    tool = web_tools(FakeBrowsers())[0]
    assert tool.name == "open_website" and tool.risk.value == "R1" and tool.idempotent is False
    assert tool.sandbox == "full-access" and tool.network and tool.timeout_s > 0
    props = tool.spec()["function"]["parameters"]["properties"]
    assert set(props) == {"site", "search", "browser"}
    assert props["browser"]["enum"] == ["default", "chrome", "edge", "firefox"]
    assert tool.description.count(".") <= 2


def test_windows_browser_lookup_returns_paths_or_none():
    found = WindowsBrowsers.find("chrome")
    assert found is None or found.name.lower() == "chrome.exe"
    assert WindowsBrowsers.find("not-a-browser") is None


# ---- 'open chrome and go to yt and search x' ----------------------------------------------------


@pytest.mark.parametrize(
    ("message", "more"),
    [
        ("open chrome and go to yt and search carrymanati", True),
        ("open chrome then search for pizza", True),
        ("open notepad and type hello", True),
        ("open steam and then go to the store", True),
        ("go to youtube", True),
        ("open steam", False),
        ("open microsoft edge", False),
        ("please open chrome", False),
        ("open steam and edge", False),
    ],
)
def test_open_app_hands_over_when_there_are_more_steps(message, more):
    assert has_more_steps(message) is more


async def test_the_users_exact_request_now_gets_chrome_and_the_youtube_search(tmp_path, key):  # noqa: F811
    text = "open chrome and go to yt and search carrymanati"
    first = ChatResult(
        tool_calls=[{"function": {"name": "open_app", "arguments": {"name": "chrome"}}}]
    )
    second = ChatResult(
        tool_calls=[
            {
                "function": {
                    "name": "open_website",
                    "arguments": {"site": "yt", "search": "carryminati", "browser": "chrome"},
                }
            }
        ]
    )
    vault, store, svc, chat, agent = build(tmp_path, key, [first, second, say("unused")])
    catalog, browsers = FakeCatalog(), FakeBrowsers()
    agent.tools.update({t.name: t for t in app_tools(catalog) + web_tools(browsers)})
    result = await agent.turn(Session(), text)
    assert catalog.launched == ["Google Chrome"]  # step one still happens
    assert browsers.opened == [
        ("chrome", "https://www.youtube.com/results?search_query=carryminati")
    ]
    assert len(chat.calls) == 2 and result.final_tool  # the turn did not stop after opening Chrome
    assert "YouTube" in result.text and [t.tool for t in result.trace] == [
        "open_app",
        "open_website",
    ]
    first_tool_msg = chat.calls[1]["messages"][-1]
    assert first_tool_msg["content"] == "Opened Google Chrome."  # the model saw it and continued
    vault.close()


async def test_a_plain_open_app_still_ends_the_turn_immediately(tmp_path, key):  # noqa: F811
    one = ChatResult(
        tool_calls=[{"function": {"name": "open_app", "arguments": {"name": "chrome"}}}]
    )
    vault, store, svc, chat, agent = build(tmp_path, key, [one])
    agent.tools.update({t.name: t for t in app_tools(FakeCatalog())})
    result = await agent.turn(Session(), "open chrome")
    assert len(chat.calls) == 1 and result.text == "Opened Google Chrome."
    vault.close()
