"""Skill: make animations and small interactive pages that open in the browser.

Two tools, both writing ONE self-contained .html file (no libraries, no downloads, nothing to
install) into Documents\\Ragnarok and opening it:

  create_animation   an interactive sorting animation (bubble, selection, insertion, merge, quick).
                     The steps are worked out here, in Python, and the page only plays them back,
                     so the animation is always a correct run of the algorithm.
  create_webpage     any other page, game or animation the model writes as HTML. The file is given
                     a Content-Security-Policy that blocks every network request, so a page the
                     model wrote cannot send anything anywhere.

This is the reliable route for "make me an animation of ...": it needs no Python packages, no
`pip install` and no running of generated programs.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.skills.documents import _write, safe_stem, target_for
from ragnarok.skills.fileops import open_with_default_program
from ragnarok.skills.folders import documents_folder
from ragnarok.skills.steps import reply

MAX_VALUES = 40
DEFAULT_VALUES = [34, 7, 23, 32, 5, 62, 14, 48, 9, 27, 41, 18]
MAX_HTML_CHARS = 80_000
CSP = (
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
    "img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
)
ALGORITHMS = {
    "bubble": ("Bubble sort", "O(n²) time, O(1) space"),
    "selection": ("Selection sort", "O(n²) time, O(1) space"),
    "insertion": ("Insertion sort", "O(n²) time, O(1) space"),
    "merge": ("Merge sort", "O(n log n) time"),
    "quick": ("Quick sort", "O(n log n) average time"),
}


# ---- working out the steps ----------------------------------------------------------------------


class _Recorder:
    """Runs an algorithm on bar ids and records a frame for every compare and every move."""

    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.a = list(range(len(values)))  # the bars, left to right, as ids
        self.done: set[int] = set()
        self.compares = 0
        self.moves = 0
        self.frames: list[dict] = []

    def v(self, pos: int) -> int:
        return self.values[self.a[pos]]

    def frame(self, note: str, cmp: tuple[int, ...] = (), kind: str = "mark") -> None:
        if kind == "cmp":
            self.compares += 1
        elif kind == "move":
            self.moves += 1
        self.frames.append(
            {
                "o": list(self.a),
                "c": list(cmp),
                "k": kind,
                "d": sorted(self.done),
                "t": note,
                "nc": self.compares,
                "nm": self.moves,
            }
        )

    def compare(self, i: int, j: int, note: str = "") -> None:
        self.frame(note or f"Compare {self.v(i)} and {self.v(j)}", (i, j), "cmp")

    def swap(self, i: int, j: int) -> None:
        self.a[i], self.a[j] = self.a[j], self.a[i]
        self.frame(f"Swap {self.v(j)} and {self.v(i)}", (i, j), "move")


def steps_for(algorithm: str, values: list[int]) -> list[dict]:
    """Every frame of `algorithm` sorting `values`; the last frame has everything sorted."""
    r = _Recorder(values)
    n = len(values)
    r.frame("Start: the bars are unsorted")
    if algorithm == "bubble":
        for end in range(n - 1, 0, -1):
            for j in range(end):
                r.compare(j, j + 1)
                if r.v(j) > r.v(j + 1):
                    r.swap(j, j + 1)
            r.done.add(end)
            r.frame(f"{r.v(end)} has bubbled to its final place")
    elif algorithm == "selection":
        for i in range(n - 1):
            low = i
            for j in range(i + 1, n):
                r.compare(j, low, f"Is {r.v(j)} smaller than the smallest so far, {r.v(low)}?")
                if r.v(j) < r.v(low):
                    low = j
            if low != i:
                r.swap(i, low)
            r.done.add(i)
            r.frame(f"{r.v(i)} is in its final place")
    elif algorithm == "insertion":
        r.done.add(0)
        for i in range(1, n):
            j = i
            while j > 0:
                r.compare(j - 1, j, f"Does {r.v(j)} belong before {r.v(j - 1)}?")
                if r.v(j - 1) > r.v(j):
                    r.swap(j - 1, j)
                    j -= 1
                else:
                    break
            r.done.add(i)
            r.frame(f"The first {i + 1} bars are now in order")
    elif algorithm == "quick":

        def quick(lo: int, hi: int) -> None:
            if lo > hi:
                return
            if lo == hi:
                r.done.add(lo)
                return
            store = lo
            r.frame(f"Pivot is {r.v(hi)}: smaller bars go to its left")
            for j in range(lo, hi):
                r.compare(j, hi, f"Is {r.v(j)} smaller than the pivot {r.v(hi)}?")
                if r.v(j) < r.v(hi):
                    if store != j:
                        r.swap(store, j)
                    store += 1
            if store != hi:
                r.swap(store, hi)
            r.done.add(store)
            r.frame(f"The pivot {r.v(store)} is in its final place")
            quick(lo, store - 1)
            quick(store + 1, hi)

        quick(0, n - 1)
    elif algorithm == "merge":

        def merge(lo: int, mid: int, hi: int) -> None:
            i, j = lo, mid + 1
            while i <= mid and j <= hi:
                r.compare(i, j, f"Merge: is {r.v(i)} (left) <= {r.v(j)} (right)?")
                if r.v(i) <= r.v(j):
                    i += 1
                else:
                    moved = r.a.pop(j)
                    r.a.insert(i, moved)
                    r.frame(f"Move {r.v(i)} in front of the larger bars", (i,), "move")
                    i += 1
                    mid += 1
                    j += 1

        def sort(lo: int, hi: int) -> None:
            if lo >= hi:
                return
            mid = (lo + hi) // 2
            sort(lo, mid)
            sort(mid + 1, hi)
            merge(lo, mid, hi)
            r.frame(f"Positions {lo + 1} to {hi + 1} are merged in order")

        sort(0, n - 1)
    else:
        raise ValueError(f"unknown algorithm {algorithm!r}")
    r.done = set(range(n))
    r.frame("Sorted!")
    return r.frames


# ---- the page ------------------------------------------------------------------------------------

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">__CSP__
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#262d38;--text:#e6edf3;--dim:#8b98a8;--v:#a78bfa;--b:#60a5fa;
--c:#22d3ee;--cmp:#e8b95f;--mov:#f08080;--ok:#78d696}
@media (prefers-color-scheme:light){:root{--bg:#f6f8fa;--panel:#fff;--line:#d8dee4;--text:#1f2328;
--dim:#59636e;--v:#7c5cf0;--b:#2f7de1;--c:#0891b2;--cmp:#b7791f;--mov:#d14b4b;--ok:#2f9e5f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,Segoe UI,sans-serif;display:flex;justify-content:center;padding:24px 16px}
main{width:100%;max-width:880px}h1{margin:0 0 2px;font-size:22px;
background:linear-gradient(90deg,var(--v),var(--b),var(--c));-webkit-background-clip:text;
background-clip:text;color:transparent}.sub{color:var(--dim);margin:0 0 16px}
#stage{position:relative;height:340px;background:var(--panel);border:1px solid var(--line);
border-radius:12px;overflow:hidden}
.bar{position:absolute;bottom:22px;border-radius:6px 6px 0 0;transition:left .28s ease,background .15s;
background:linear-gradient(180deg,var(--b),var(--v));min-width:4px}
.bar span{position:absolute;top:-18px;left:0;right:0;text-align:center;font-size:11px;color:var(--dim)}
.bar.cmp{background:var(--cmp)}.bar.mov{background:var(--mov)}.bar.done{background:var(--ok)}
#note{min-height:28px;margin:14px 2px 6px;font-weight:600}
#stats{color:var(--dim);font-size:13px;margin:0 2px 12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:8px;
padding:8px 14px;font:inherit;cursor:pointer}button:hover{border-color:var(--v)}
button.main{background:linear-gradient(90deg,var(--v),var(--b));border:0;color:#fff;font-weight:600}
label{color:var(--dim);font-size:13px;display:flex;gap:8px;align-items:center}
input[type=range]{accent-color:var(--v)}#scrub{flex:1;min-width:160px}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-top:14px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
</style></head><body><main>
<h1>__NAME__</h1><p class="sub">__COMPLEXITY__ &middot; press Space to play, &larr; &rarr; to step</p>
<div id="stage" aria-label="bars being sorted"></div>
<div id="note" aria-live="polite"></div><div id="stats"></div>
<div class="row"><button id="back">&#9198; Back</button><button class="main" id="play">&#9654; Play</button>
<button id="next">Step &#9197;</button><button id="again">&#8634; Restart</button>
<label>Speed <input id="speed" type="range" min="1" max="10" value="6"></label>
<input id="scrub" type="range" min="0" value="0"></div>
<div class="legend"><span><i style="background:var(--b)"></i>unsorted</span>
<span><i style="background:var(--cmp)"></i>being compared</span>
<span><i style="background:var(--mov)"></i>moved</span>
<span><i style="background:var(--ok)"></i>in its final place</span></div>
</main>
<script>
const VALUES=__VALUES__,FRAMES=__FRAMES__;
const stage=document.getElementById('stage'),note=document.getElementById('note'),
stats=document.getElementById('stats'),play=document.getElementById('play'),
scrub=document.getElementById('scrub'),speed=document.getElementById('speed');
const n=VALUES.length,max=Math.max(...VALUES),bars=VALUES.map((v,id)=>{
const b=document.createElement('div');b.className='bar';
b.innerHTML=n<=24?'<span>'+v+'</span>':'';stage.appendChild(b);return b});
function layout(){const w=stage.clientWidth,slot=w/n,gap=Math.min(6,slot*.18),h=stage.clientHeight-50;
bars.forEach((b,id)=>{b.style.width=Math.max(4,slot-gap)+'px';
b.style.height=Math.max(6,VALUES[id]/max*h)+'px'})}
let i=0,timer=null;scrub.max=FRAMES.length-1;
function draw(){const f=FRAMES[i],w=stage.clientWidth,slot=w/n;
f.o.forEach((id,pos)=>{const b=bars[id];b.style.left=(pos*slot+Math.min(6,slot*.18)/2)+'px';
let cls='bar';if(f.d.includes(pos))cls+=' done';
if(f.c.includes(pos))cls+=f.k==='move'?' mov':' cmp';b.className=cls});
note.textContent=f.t;stats.textContent='Step '+i+' of '+(FRAMES.length-1)+
'  ·  comparisons: '+f.nc+'  ·  moves: '+f.nm;scrub.value=i}
function stop(){clearInterval(timer);timer=null;play.innerHTML='&#9654; Play'}
function tick(){if(i>=FRAMES.length-1){stop();return}i++;draw()}
function start(){if(i>=FRAMES.length-1)i=0;play.innerHTML='&#9208; Pause';
timer=setInterval(tick,1100-speed.value*100)}
play.onclick=()=>timer?stop():start();
document.getElementById('next').onclick=()=>{stop();tick()};
document.getElementById('back').onclick=()=>{stop();if(i>0){i--;draw()}};
document.getElementById('again').onclick=()=>{stop();i=0;draw();start()};
speed.oninput=()=>{if(timer){stop();start()}};
scrub.oninput=()=>{stop();i=+scrub.value;draw()};
addEventListener('keydown',e=>{if(e.key===' '){e.preventDefault();play.onclick()}
else if(e.key==='ArrowRight')document.getElementById('next').onclick();
else if(e.key==='ArrowLeft')document.getElementById('back').onclick()});
addEventListener('resize',()=>{layout();draw()});layout();draw();
</script></body></html>
"""


def build_animation_page(algorithm: str, values: list[int], title: str | None = None) -> str:
    name, complexity = ALGORITHMS[algorithm]
    frames = steps_for(algorithm, values)
    data = json.dumps(frames, separators=(",", ":")).replace("</", "<\\/")
    page_title = title or f"{name} animation"
    return (
        _PAGE.replace("__CSP__", CSP)
        .replace("__TITLE__", _html_escape(page_title))
        .replace("__NAME__", _html_escape(page_title))
        .replace("__COMPLEXITY__", complexity)
        .replace("__VALUES__", json.dumps(values))
        .replace("__FRAMES__", data)
    )


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def locked_down(html: str) -> str:
    """The page with a policy that blocks all network requests, added if it is not there."""
    if "Content-Security-Policy" in html:
        return html
    head = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head:
        return html[: head.end()] + CSP + html[head.end() :]
    html_tag = re.search(r"<html[^>]*>", html, re.IGNORECASE)
    if html_tag:
        return html[: html_tag.end()] + "<head>" + CSP + "</head>" + html[html_tag.end() :]
    return (
        f'<!doctype html><html><head><meta charset="utf-8">{CSP}</head><body>{html}</body></html>'
    )


# ---- the tools -----------------------------------------------------------------------------------


class CreateAnimationArgs(ToolArgs):
    algorithm: Literal["bubble", "selection", "insertion", "merge", "quick"] = Field(
        description="Which sorting algorithm to animate."
    )
    values: list[int] | None = Field(
        default=None,
        max_length=MAX_VALUES,
        description="The numbers to sort (1 to 999). Leave out for a good default set.",
    )
    title: str | None = Field(default=None, max_length=100, description="A page title, if asked.")
    open: bool = Field(default=True, description="Open the animation in the browser.")

    @field_validator("algorithm", mode="before")
    @classmethod
    def _algorithm_name(cls, value: object) -> object:
        if isinstance(value, str):
            word = re.sub(r"[\s_-]*sort(ing)?$", "", value.strip().lower())
            return {"quicksort": "quick", "mergesort": "merge"}.get(word, word)
        return value

    @field_validator("values", mode="before")
    @classmethod
    def _values_from_text(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(x) for x in re.findall(r"-?\d+", value)] or None
        return value

    @field_validator("values")
    @classmethod
    def _values_in_range(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (len(value) < 2 or not all(1 <= v <= 999 for v in value)):
            raise ValueError("give 2 to 40 whole numbers between 1 and 999")
        return value


class CreateWebpageArgs(ToolArgs):
    title: str = Field(min_length=2, max_length=100, description="The page's title.")
    html: str = Field(
        min_length=20,
        max_length=MAX_HTML_CHARS,
        description="The complete page: one HTML file with its CSS in <style> and its JavaScript "
        "in <script>. No external links, libraries or images from the web.",
    )
    open: bool = Field(default=True, description="Open the page in the browser.")


def webpage_tools(
    documents: Path | None = None, opener: Callable[[Path], None] | None = None
) -> list[Tool]:
    folder = (documents or documents_folder()) / "Ragnarok"
    opener = opener or open_with_default_program

    async def save(title: str, html: str, want_open: bool, noun: str):
        data = html.encode("utf-8")
        try:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            path, already = await asyncio.to_thread(
                target_for, folder, safe_stem(title), "html", data
            )
            if not already:
                await asyncio.to_thread(_write, path, data)
        except OSError as exc:
            return f"error: I couldn't save the {noun} ({type(exc).__name__})."
        text = f"{'Already created' if already else 'Created'} {path.name} in {folder}"
        if want_open:
            try:
                await asyncio.to_thread(opener, path)
                text += " and opened it in your browser"
            except OSError:
                text += ", but I couldn't open it; double-click the file"
        return reply(text + ".")

    async def create_animation(args: CreateAnimationArgs):
        values = args.values or DEFAULT_VALUES
        html = await asyncio.to_thread(build_animation_page, args.algorithm, values, args.title)
        title = args.title or f"{ALGORITHMS[args.algorithm][0]} animation"
        return await save(title, html, args.open, "animation")

    async def create_webpage(args: CreateWebpageArgs):
        return await save(args.title, locked_down(args.html), args.open, "page")

    common = {
        "risk": Risk.R1,  # a new file and a browser window; closing the window undoes it
        "idempotent": True,
        "sandbox": "full-access",  # the browser that opens the page runs as the user
        "fs": {"write": [str(folder)]},
    }
    return [
        Tool(
            name="create_animation",
            description="Make an interactive animation of a sorting algorithm (bubble, selection, "
            "insertion, merge or quick sort) and open it in the browser. Use it whenever the "
            "user asks for an animation or visualization of one of these.",
            args_model=CreateAnimationArgs,
            timeout_s=30,
            handler=create_animation,
            **common,
        ),
        Tool(
            name="create_webpage",
            description="Save a page you write as one HTML file (CSS and JavaScript inside it) and "
            "open it in the browser. Use it for any other animation, visualization, game or "
            "small web page; never use Python packages or pip for these.",
            args_model=CreateWebpageArgs,
            timeout_s=30,
            handler=create_webpage,
            **common,
        ),
    ]
