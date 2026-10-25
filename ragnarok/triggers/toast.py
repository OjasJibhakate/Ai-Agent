"""A Windows desktop notification, with no extra library (PowerShell and the WinRT toast API).

Best effort by design: if it cannot be shown (not Windows, no PowerShell, notifications off) nothing
happens, because the Inbox already holds the message. The text goes in through the environment, not
the command line, so it can never be run as code.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text id="1"></text><text id="2"></text></binding></visual></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:RAG_TOAST_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:RAG_TOAST_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)
"""


async def toast(title: str, body: str = "") -> bool:
    """Show a notification; True if Windows accepted it."""
    if sys.platform != "win32" or os.environ.get("RAGNAROK_NO_TOAST") == "1":
        return False
    env = {
        **os.environ,
        "RAG_TOAST_TITLE": title[:100],
        "RAG_TOAST_BODY": body[:200],
    }
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT,
            env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        await asyncio.wait_for(proc.wait(), 15)
        return proc.returncode == 0
    except (OSError, TimeoutError):
        with contextlib.suppress(Exception):
            proc.kill()  # type: ignore[possibly-undefined]
        return False
