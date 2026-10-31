"""
title: Voice reply
author: selvalogesh
version: 1.0.0
description: A toggle in the chat input. When it's on, each reply also arrives as a voice note: Kokoro reads it on the server and a player is attached to the message (CR-1 §3.4, R16).
"""

import base64
import html
import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger("voice_note")

ICON = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    "stroke='currentColor' stroke-width='2'><path d='M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z'/>"
    "<path d='M19 11a7 7 0 0 1-14 0M12 18v3'/></svg>"
)

PLAYER = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;font-family:system-ui,sans-serif;background:transparent}}
.note{{display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:18px;background:#dcf8c6;
max-width:420px;color:#111}}
@media (prefers-color-scheme:dark){{.note{{background:#005c4b;color:#eee}}}}
audio{{flex:1;height:36px}}
.len{{font-size:12px;opacity:.7;white-space:nowrap}}
</style></head><body><div class="note"><audio controls preload="metadata" src="{src}"></audio>
<span class="len">{label}</span></div></body></html>"""


def speakable(text: str) -> str:
    """Strips reasoning, code and markdown so Kokoro reads only the prose."""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    text = re.sub(r"<details.*?</details>", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links keep their text
    text = re.sub(r"[*_`~]+", "", text)  # emphasis marks sit inside sentences
    text = re.sub(r"[#>|]+", " ", text)  # headings, quotes and table bars separate words
    return re.sub(r"\s+", " ", text).strip()


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 2 else cut) + " The rest is in the text."


class Filter:
    class Valves(BaseModel):
        priority: int = 10
        KOKORO_URL: str = Field(default="http://kokoro:8880/v1", description="Kokoro's OpenAI-compatible API")
        VOICE: str = "af_heart"
        SPEED: float = 1.0
        MAX_CHARS: int = Field(default=1200, description="Longer replies are cut at a sentence (about 75 s of audio)")
        TIMEOUT_SECONDS: int = 180

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True
        self.icon = ICON
        self.transport: Optional[httpx.AsyncBaseTransport] = None  # tests inject a mock transport

    async def outlet(self, body: dict, __event_emitter__=None) -> dict:
        messages = body.get("messages") or []
        if not messages or messages[-1].get("role") != "assistant" or not __event_emitter__:
            return body
        text = shorten(speakable(messages[-1].get("content") or ""), self.valves.MAX_CHARS)
        if not text.replace("(code omitted)", "").strip():
            return body  # nothing but code: no voice note

        await __event_emitter__({"type": "status", "data": {"description": "Recording voice note…", "done": False}})
        try:
            audio = await self._speak(text)
        except Exception as e:
            log.warning("voice_note: Kokoro failed: %s", e)
            await __event_emitter__(
                {"type": "status", "data": {"description": "Voice note unavailable right now", "done": True}}
            )
            return body

        seconds = len(audio) * 8 / 128000  # Kokoro sends 128 kbit/s mp3
        label = f"{int(seconds // 60)}:{int(seconds % 60):02d}"
        src = "data:audio/mpeg;base64," + base64.b64encode(audio).decode()
        await __event_emitter__({"type": "embeds", "data": {"embeds": [PLAYER.format(src=src, label=html.escape(label))]}})
        await __event_emitter__({"type": "status", "data": {"description": "Voice note ready", "done": True}})
        return body

    async def _speak(self, text: str) -> bytes:
        async with httpx.AsyncClient(transport=self.transport, timeout=self.valves.TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self.valves.KOKORO_URL}/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": self.valves.VOICE,
                    "speed": self.valves.SPEED,
                    "response_format": "mp3",
                    "stream": False,
                },
            )
        response.raise_for_status()
        return response.content
