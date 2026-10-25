"""The two screen tools the chat model can call.

read_screen   looks at the screen and answers a question about it. It works only when the user's
              own message asks about the screen, so text in a file, a web page or a memory cannot
              make the agent look (and cannot use what it saw to steer anything).
use_computer  does a task by looking and using the mouse and keyboard. R2: the user must approve
              each task, every step is checked and logged first, and Esc or moving the mouse
              stops it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import (
    CONFIRM,
    CURRENT_TEXT,
    EVENT_LOG,
    PROGRESS,
    Tool,
    ToolArgs,
    ToolResult,
)
from ragnarok.skills.screen.capture import DETAIL_WIDTH, MAX_WIDTH, ScreenSource, WindowsScreen
from ragnarok.skills.screen.input import WindowsInput
from ragnarok.skills.screen.task import Driver, GatewayVision, ScreenTask, Vision, WindowOps
from ragnarok.skills.screen.winapi import Windows

MAX_DELAY_S = 10
ASKS_ABOUT_SCREEN = re.compile(
    r"\b(screen|display|monitor|desktop|window|windows?\b.*\bopen|tab)\b"
    r"|\b(this|current|open|that|the)\s+(page|dialog|error|message|document|site|website|email|mail|slide|"
    r"popup|pop-up|notification|form|table|chart|image|picture|video)\b"
    r"|\bwhat\s+(am\s+i|are\s+you|do\s+you)\s+(looking|seeing|see)\b"
    r"|\b(look|looking)\s+at\b|\bwhat'?s\s+(on|open|showing|happening)\b|\bread\s+(this|it|that)\b",
    re.IGNORECASE,
)
APPROVAL_NOTE = (
    "This lets me look at your screen and use your mouse and keyboard until the task is done "
    "(at most 15 steps). Hold Esc or move the mouse to stop me. I never type into a terminal "
    "or type a password. This is experimental: I often miss small buttons and list rows, so "
    "watch what I do."
)


# "read my screen AND THEN save the total": reading is only the first step.
FOLLOW_UP = re.compile(
    r"\b(and|then|after that|also)\b.{0,80}\b(save|remember|note|copy|paste|add|open|search|go|"
    r"send|email|write|create|schedule|book|find|look up|google|type|click|play|fill|download|"
    r"upload|post|forward|reply|call|message)\b",
    re.IGNORECASE,
)


def asks_about_screen(user_text: str) -> bool:
    """Did the user's own message ask about what is on the screen?"""
    return bool(ASKS_ABOUT_SCREEN.search(user_text))


def has_follow_up(user_text: str) -> bool:
    return bool(FOLLOW_UP.search(user_text))


class ReadScreenArgs(ToolArgs):
    question: str = Field(
        default="Describe what is on the screen.",
        max_length=300,
        description="What to find out, such as 'what does the error say' or 'summarise this page'.",
    )
    detail: Literal["normal", "high"] = Field(
        default="normal", description="high reads very small print but takes longer."
    )
    delay_seconds: int = Field(
        default=0, ge=0, le=MAX_DELAY_S, description="Wait first, so the user can switch windows."
    )


class UseComputerArgs(ToolArgs):
    task: str = Field(
        min_length=3, max_length=300, description="The task, in one sentence, as the user said it."
    )


def screen_tools(
    gateway: Any = None,
    *,
    screen: ScreenSource | None = None,
    driver: Driver | None = None,
    windows: WindowOps | None = None,
    vision: Vision | None = None,
    settle: float = 0.7,
) -> list[Tool]:
    screen = screen or WindowsScreen()
    driver = driver or WindowsInput()
    windows = windows or Windows()
    vision = vision or GatewayVision(gateway)

    async def _capture(width: int, delay: int) -> Any:
        """Grab one frame, moving the user's terminal out of the picture if it is in front."""
        if delay:
            say = PROGRESS.get()
            if say:
                await say(f"looking at your screen in {delay}s: switch to what you want me to read")
            await asyncio.sleep(delay)
        hidden = None
        front = await asyncio.to_thread(windows.foreground)
        if front is not None and windows.is_own_terminal(front):
            await asyncio.to_thread(windows.minimize, front.hwnd)
            await asyncio.sleep(0.6)
            hidden = front.hwnd
        try:
            return await asyncio.to_thread(screen.grab, width)
        finally:
            if hidden is not None:
                windows.restore(hidden)

    async def read_screen(args: ReadScreenArgs) -> str | ToolResult:
        user_text = CURRENT_TEXT.get()
        if not asks_about_screen(user_text):
            return "error: I only look at your screen when you ask me to."
        log = EVENT_LOG.get()
        if log:  # a record that the screen was read, before it is: the question, never the pixels
            await log("screen_read", f"{args.question[:120]} (detail {args.detail})")
        width = DETAIL_WIDTH if args.detail == "high" else MAX_WIDTH
        frame = await _capture(width, args.delay_seconds)
        png = await asyncio.to_thread(frame.png)
        del frame  # screenshots exist only for the length of this call
        answer = await vision.answer(png, args.question)
        if not answer:
            return "error: I could not read the screen"
        if has_follow_up(user_text):  # the model continues with it: mark it as data
            return f"<screen_content>\n{answer}\n</screen_content>"
        return ToolResult(answer, final=True)

    async def use_computer(args: UseComputerArgs) -> str | ToolResult:
        loop = ScreenTask(
            screen,
            driver,
            windows,
            vision,
            log=EVENT_LOG.get(),
            progress=PROGRESS.get(),
            confirm=CONFIRM.get(),
            settle=settle,
        )
        result = await loop.run(args.task, CURRENT_TEXT.get())
        tail = f" ({result.steps} steps, {result.seconds:.0f}s)" if result.steps else ""
        return ToolResult(result.summary + tail, final=True)

    return [
        Tool(
            name="read_screen",
            description="Look at the user's screen and answer a question about what is on it. "
            "Use it when they ask what is on screen or want something on it read or explained.",
            args_model=ReadScreenArgs,
            risk=Risk.R1,
            idempotent=True,
            timeout_s=90,
            sandbox="read-only",
            handler=read_screen,
        ),
        Tool(
            name="use_computer",
            description="Do a task by looking at the screen and using the mouse and keyboard, "
            "after the user approves. Use it only when no other tool can do the task.",
            args_model=UseComputerArgs,
            risk=Risk.R2,
            idempotent=False,
            timeout_s=240,
            sandbox="full-access",
            handler=use_computer,
            approval_note=APPROVAL_NOTE,
        ),
    ]
