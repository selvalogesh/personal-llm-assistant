"""
title: Router
author: selvalogesh
version: 1.2.0
description: Sends each chat turn to OpenRouter through a chain of models. A cheap classifier first works out whether the reply should be an image, a web answer, code that runs or a voice note (CR-4). Non-logging providers first, the free chain when credits or paid routes fail, and a plain message instead of a raw error (CR-1 §3.1).
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger("router")

# Flags the toggle filters (web_search.py, image_mode.py) set on the request body.
WEB_FLAG = "router_web_search"
IMAGE_FLAG = "router_image_mode"

# ---------- the classifier (CR-4 §3.1) ----------

# One line each: message → formats. Kept apart from the labelled set in tests/, which measures them (CR-4 §4, O5).
SEED_EXAMPLES = """\
"paint me a watercolour of a lotus pond" → image
"design a poster for a school science fair" → image
"turn that into a cartoon style" (after an image) → image
"what does that image show?" (after an image) → chat
"imagine you're a dragon and talk to me" → chat
"draw up a contract for a rental flat" → chat
"how do I draw a horse step by step?" → chat
"what's the score in the India match right now?" → web
"look into the reviews of the new Kia Seltos" → web
"who is the CEO of Infosys?" → web
"what year did the Berlin Wall fall?" → chat
"write a JavaScript function to debounce input" → chat
"chart my savings: 2000, 3500, 5000, 4200" → code
"solve x^3 - 6x^2 + 11x - 6 = 0 exactly" → code
"what's 2 + 2?" → chat
"send me the answer as audio" → voice once
"keep replying in voice from now on" → voice on
"no more voice notes please" (voice mode on) → voice off
"tell me a joke" (voice mode on) → chat
"search the web for today's gold price and read it out" → web, voice once
"no, I wanted a picture, not a description" (after a text reply) → image, corrects previous: image
"you didn't look it up, check online" (after a text reply) → web, corrects previous: web"""

CLASSIFIER_PROMPT = f"""You route one chat message to the formats its reply needs. Reply with JSON only:
{{"image": bool, "image_prompt": string, "web": bool, "code": bool, "voice": "none"|"once"|"on"|"off", "corrects_previous": null | {{"image": bool, "web": bool, "code": bool, "voice": "none"|"once"|"on"|"off"}}}}

- image: the user wants a picture generated now (draw, paint, generate, picture/photo/logo of, or a change to the last generated image). Not for describing, roleplay, "draw a conclusion", or how-to-draw questions. image_prompt: a complete, standalone description for an image model, using the chat's context for follow-ups; "" when image is false.
- web: the answer needs current or fresh information (news, prices, weather, scores, recent releases, "research", "look up", "search"), or facts that change over time. Not for stable knowledge.
- code: Python must actually run to answer: exact or non-trivial calculations, data analysis, charts or plots, or the user asks to run code. Not for writing or explaining code, and not for simple arithmetic.
- voice: "once" when the user wants this reply as audio or spoken; "on" when they want voice replies from now on; "off" when they want voice replies to stop; otherwise "none".
- corrects_previous: when this message complains that the previous reply had the wrong format, the formats the previous reply should have had; otherwise null.
Several can be true at once. Plain chat is all false and voice "none".

Examples (message → formats; anything not listed is false):
{SEED_EXAMPLES}"""

VOICES = ("none", "once", "on", "off")
_LABELS = {"image": {"type": "boolean"}, "web": {"type": "boolean"}, "code": {"type": "boolean"}, "voice": {"type": "string", "enum": list(VOICES)}}
INTENT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "formats",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["image", "image_prompt", "web", "code", "voice", "corrects_previous"],
            "properties": {
                **_LABELS,
                "image_prompt": {"type": "string"},
                "corrects_previous": {
                    "anyOf": [
                        {"type": "null"},
                        {"type": "object", "additionalProperties": False, "required": list(_LABELS), "properties": _LABELS},
                    ]
                },
            },
        },
    },
}

PLAIN = {"image": False, "image_prompt": "", "web": False, "code": False, "voice": "none", "corrects": None}
FORMAT_NAMES = {"image": "image", "web": "web search", "code": "code"}  # for the footer
VOICE_NAMES = {"once": "voice", "on": "voice on", "off": "voice off"}

TEXT_LIMIT = 300  # characters of a message the store keeps, and that go back to the classifier (CR-4 D5)
MESSAGE_LIMIT = 2000  # characters of a long message the classifier sees: its start and its end
TURN_SECONDS = 86400  # a chat's last turn, kept to compare with a correction
MISS_SECONDS = 90 * 86400

CHAINS = {
    # id: (picker name, valve holding the comma-separated chain)
    "auto": ("Auto", "AUTO_MODELS"),
    "creative": ("Creative", "CREATIVE_MODELS"),
    "smart": ("Smart", "SMART_MODELS"),
    "free": ("Free", "FREE_MODELS"),
}

FRIENDLY = {
    "auth": "The server's OpenRouter key was rejected. Ask the admin to check `secrets/openrouter-api-key`.",
    "credits": "OpenRouter credits have run out and the free models couldn't answer either. Ask the admin to top up, or try again later.",
    "budget": "This month's spending budget is used up, and the free models couldn't answer either. It resets at the start of next month.",
    "busy": "Every model in this chain is busy right now. Try again in a minute.",
    "network": "The server couldn't reach OpenRouter. Try again in a minute.",
    "key_missing": "The server has no OpenRouter key yet. Ask the admin to fill in `secrets/openrouter-api-key`.",
    "other": "None of the models could answer this time. Try again, or pick another option.",
    # Notes under a reply the length limit cut off (finish_reason "length")
    "cut_thinking": "The model used its whole reply limit thinking and never wrote the answer. Ask again, or split the request into smaller steps.",
    "cut": "The reply hit the length limit and stops here. Say \"continue\" to get the rest.",
    "stopped": "Stopped after {minutes} of thinking, as you asked. Ask again, or split the request into smaller steps.",
}

# Low credits: OpenRouter refuses a request whose full output it can't cover, and says how much it can.
AFFORD = re.compile(r"can only afford (\d+)")
MIN_AFFORD = 1000  # below this a reply is barely a paragraph: the free chain answers better


class RouteError(Exception):
    """A failed attempt: `kind` picks the next step and the message the user sees."""

    def __init__(self, kind: str, detail: str = "", status: int = 0):
        super().__init__(f"{kind} ({status}): {detail[:300]}")
        self.kind = kind
        self.detail = detail
        self.status = status


def classify_error(status: int, detail: str) -> str:
    text = detail.lower()
    if status == 401:
        return "auth"
    if status == 402 or "credits" in text or "can only afford" in text:
        return "credits"
    if status == 403 and ("budget" in text or "limit" in text):
        return "budget"
    if "data policy" in text or "no endpoints found" in text or "no allowed providers" in text:
        return "privacy"
    if status == 429:
        return "busy"
    return "other"


def minutes(seconds: float) -> str:
    n = max(1, round(seconds / 60))
    return f"{n} minute" if n == 1 else f"{n} minutes"


def split_models(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def batches(models: list[str], size: int = 3) -> list[list[str]]:
    """OpenRouter's `models` takes at most 3 entries, so longer chains go in turns."""
    return [models[i : i + size] for i in range(0, len(models), size)] or [[]]


def has_images(messages: list) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(part.get("type") == "image_url" for part in content):
            return True
    return False


def message_text(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content if part.get("type") == "text")


def last_user_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message_text(message)
    return ""


def previous_turn(messages: list) -> tuple[str, bool]:
    """The user message before the last one, and whether the reply to it was an image."""
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(users) < 2:
        return "", False
    replies = [m for m in messages[users[-2] + 1 : users[-1]] if m.get("role") == "assistant"]
    return message_text(messages[users[-2]]).strip(), any("![" in message_text(m) for m in replies)


def excerpt(text: str, limit: int) -> str:
    """A long message's start and end: what it asks for is usually in one of them."""
    if len(text) <= limit:
        return text
    return f"{text[: limit // 2]} … {text[-limit // 2 :]}"


def labels_of(raw: dict) -> dict:
    voice = raw.get("voice")
    return {
        "image": bool(raw.get("image")),
        "web": bool(raw.get("web")),
        "code": bool(raw.get("code")),
        "voice": voice if voice in VOICES else "none",
    }


def read_intent(raw: dict, voice_on: bool, has_previous: bool) -> dict:
    """The classifier's answer, read with care (CR-4 D16)."""
    intent = {**PLAIN, **labels_of(raw)}
    if intent["voice"] == ("on" if voice_on else "off"):
        intent["voice"] = "none"  # the mode the chat is already in changes nothing
    if intent["image"]:
        intent["image_prompt"] = str(raw.get("image_prompt") or "").strip()
    corrects = raw.get("corrects_previous")
    if has_previous and isinstance(corrects, dict):
        intent["corrects"] = labels_of(corrects)
    return intent


def describe(labels: dict) -> str:
    """Labels as the examples write them: "web, voice once", or "chat"."""
    parts = [name for name in ("image", "web", "code") if labels[name]]
    if labels["voice"] != "none":
        parts.append(f"voice {labels['voice']}")
    return ", ".join(parts) or "chat"


def format_names(intent: dict) -> list[str]:
    names = [name for key, name in FORMAT_NAMES.items() if intent[key]]
    return names + ([VOICE_NAMES[intent["voice"]]] if intent["voice"] in VOICE_NAMES else [])


class Store:
    """The Router's own SQLite file (CR-4 §3.7): each chat's last turn and voice mode, and the classifier's misses.
    Old rows go when their table is written to."""

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS turns (
            chat_id TEXT PRIMARY KEY, user_id TEXT, message_id TEXT, text TEXT, labels TEXT, at REAL);
        CREATE TABLE IF NOT EXISTS voice_chats (chat_id TEXT PRIMARY KEY, at REAL);
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY, at REAL, user_id TEXT, chat_id TEXT, message_id TEXT, text TEXT, labels TEXT,
            signal TEXT, strong INTEGER);
        CREATE INDEX IF NOT EXISTS detections_by_user ON detections (user_id, strong, at);
    """

    def __init__(self, path: str):
        self.path = path
        self._ready = False

    @contextmanager
    def _db(self):
        if not self._ready:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        try:
            with con:
                if not self._ready:
                    con.executescript(self.SCHEMA)
                    self._ready = True
                yield con
        finally:
            con.close()

    def last_turn(self, chat_id: str) -> Optional[dict]:
        with self._db() as con:
            row = con.execute(
                "SELECT user_id, message_id, text, labels FROM turns WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row and {"user_id": row[0], "message_id": row[1], "text": row[2], "labels": json.loads(row[3])}

    def set_last_turn(self, chat_id: str, user_id: str, message_id: str, text: str, labels: dict) -> None:
        now = time.time()
        with self._db() as con:
            con.execute("DELETE FROM turns WHERE at < ?", (now - TURN_SECONDS,))
            con.execute(
                "INSERT OR REPLACE INTO turns VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, message_id, text[:TEXT_LIMIT], json.dumps(labels), now),
            )

    def voice_on(self, chat_id: str) -> bool:
        with self._db() as con:
            return con.execute("SELECT 1 FROM voice_chats WHERE chat_id = ?", (chat_id,)).fetchone() is not None

    def set_voice(self, chat_id: str, on: bool) -> None:
        with self._db() as con:
            if on:
                con.execute("INSERT OR REPLACE INTO voice_chats VALUES (?, ?)", (chat_id, time.time()))
            else:
                con.execute("DELETE FROM voice_chats WHERE chat_id = ?", (chat_id,))

    def add_miss(self, user_id, chat_id, message_id, text, labels, signal, strong) -> None:
        now, text = time.time(), text[:TEXT_LIMIT]
        with self._db() as con:
            con.execute("DELETE FROM detections WHERE at < ?", (now - MISS_SECONDS,))
            if strong:  # a newer miss on the same text replaces the older one
                con.execute("DELETE FROM detections WHERE user_id = ? AND strong = 1 AND text = ?", (user_id, text))
            con.execute(
                "INSERT INTO detections (at, user_id, chat_id, message_id, text, labels, signal, strong) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, user_id, chat_id, message_id, text, json.dumps(labels), signal, int(strong)),
            )

    def has_miss(self, message_id: str, signal: str) -> bool:
        with self._db() as con:
            row = con.execute(
                "SELECT 1 FROM detections WHERE message_id = ? AND signal = ?", (message_id, signal)
            ).fetchone()
        return row is not None

    def examples(self, user_id: str, limit: int) -> list[tuple[str, dict]]:
        """This user's latest strong misses, newest first: the classifier's learned examples (CR-4 D5)."""
        with self._db() as con:
            rows = con.execute(
                "SELECT text, labels FROM detections WHERE user_id = ? AND strong = 1 ORDER BY at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [(text, json.loads(labels)) for text, labels in rows]


def alt_text(prompt: str) -> str:
    """Markdown image alt text ends at a blank line or a bracket, so the prompt goes on one line without them."""
    return " ".join(re.sub(r"[\[\]\\]", " ", prompt).split())[:80]


class Pipe:
    class Valves(BaseModel):
        API_BASE_URL: str = "https://openrouter.ai/api/v1"
        API_KEY_FILE: str = Field(
            default="/run/chat-secrets/openrouter-api-key",
            description="File holding the OpenRouter key (read on every request, so rotating it needs no restart)",
        )
        AUTO_MODELS: str = "deepseek/deepseek-v4.1-flash, z-ai/glm-5.3, qwen/qwen3.7-flash, mistralai/mistral-small-2603"
        CREATIVE_MODELS: str = "thedrummer/cydonia-24b-v4.1, sao10k/l3.1-euryale-70b, deepseek/deepseek-v4.1-flash"
        SMART_MODELS: str = "anthropic/claude-sonnet-5.5, openai/gpt-5.6-terra, google/gemini-3.5-flash"
        FREE_MODELS: str = "google/gemma-4-31b-it:free, nvidia/nemotron-3.5-super-vl:free, nvidia/nemotron-3-ultra-550b-a55b:free"
        VISION_MODELS: str = Field(
            default="deepseek/deepseek-v4.1-flash, qwen/qwen3.7-flash, google/gemma-4-31b-it:free",
            description="Used when a message has images and no model in the chosen chain accepts them",
        )
        IMAGE_MODELS: str = Field(
            default="recraft/recraft-v4.1-flash, bytedance-seed/seedream-5-0-flash, qwen/qwen-image-2.1-turbo",
            description="Image generation, cheapest first",
        )
        TASK_MODEL: str = Field(
            default="qwen/qwen3.7-flash",
            description="Titles, tags and follow-up suggestions",
        )
        CLASSIFIER_MODEL: str = Field(
            default="deepseek/deepseek-v4.1-flash",
            description="Works out each message's formats (image, web search, code, voice) before the answer (CR-4 D15)",
        )
        CLASSIFIER_TIMEOUT_SECONDS: float = Field(
            default=4, description="A slower or failed classifier means the turn is plain chat"
        )
        LEARNED_EXAMPLES: int = Field(
            default=20, description="Each user's latest misses that go into their own classifier calls (CR-4 D5)"
        )
        STORE_PATH: str = Field(
            default="/app/backend/data/router/router.sqlite",
            description="The Router's file: voice mode per chat and the classifier's misses",
        )
        THINKING_LIMIT_SECONDS: float = Field(
            default=120,
            description="Ask the user whether to keep waiting when a model has thought this long without answering; 0 never asks",
        )
        WEB_ENGINE: str = Field(default="parallel", description="OpenRouter web plugin engine")
        WEB_MAX_RESULTS: int = 3
        SHOW_FOOTER: bool = True
        TIMEOUT_SECONDS: int = 300

    def __init__(self):
        self.valves = self.Valves()
        self.transport: Optional[httpx.AsyncBaseTransport] = None  # tests inject a mock transport
        self._vision_ids: set[str] = set()
        self._vision_fetched_at = 0.0
        self._store: Optional[Store] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._http_key: Optional[tuple] = None

    def pipes(self):
        return [{"id": chain_id, "name": name} for chain_id, (name, _) in CHAINS.items()]

    # ---------- entry point ----------

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__=None,
        __event_call__=None,
        __task__=None,
        __request__=None,
    ):
        chain_id = body.get("model", "").split(".", 1)[-1]
        if chain_id not in CHAINS:
            chain_id = "auto"
        user_id = (__user__ or {}).get("id", "")

        try:
            key = self._read_key()
        except RouteError as e:
            log.error("router: %s", e)
            return FRIENDLY[e.kind]

        if __task__:
            return await self._task(body, key, user_id)

        image_flag = bool(body.pop(IMAGE_FLAG, False))
        web_flag = bool(body.pop(WEB_FLAG, False))
        intent = await self._decide(body, key, user_id, __metadata__ or {}, image_flag, web_flag)
        if intent["image"]:
            return self._image(body, key, user_id, intent, __event_emitter__, __request__, __metadata__, __user__)
        return self._chat(body, chain_id, key, user_id, intent, __event_emitter__, __event_call__)

    # ---------- the turn's formats (CR-4 §3.1, §3.7) ----------

    async def _decide(self, body, key, user_id, metadata, image_flag, web_flag) -> dict:
        """What this turn's reply should be: the classifier's reading with the toggles on top. Also keeps the chat's
        voice mode and the miss log. Never raises: a classifier that fails or is slow means plain chat."""
        messages = body.get("messages", [])
        chat_id = metadata.get("chat_id") or ""
        text = last_user_text(messages).strip()
        prev_text, last_image = previous_turn(messages)
        voice_on = bool(chat_id) and self._remember("voice_on", chat_id, default=False)
        intent, cost = dict(PLAIN), 0.0
        if text and not image_flag:
            learned = self._remember("examples", user_id, self.valves.LEARNED_EXAMPLES, default=[]) if user_id else []
            try:
                intent, cost = await asyncio.wait_for(
                    self._classify(key, user_id, text, has_images(messages[-1:]), prev_text, last_image, voice_on, learned),
                    self.valves.CLASSIFIER_TIMEOUT_SECONDS,
                )
            except Exception as e:
                log.warning("router: classifier failed, answering as chat: %r", e)
        intent = {
            **intent,
            "image": intent["image"] or image_flag,
            "web": intent["web"] or web_flag,
            "formats": format_names(intent),
            "cost": cost,
        }
        if chat_id:
            await self._record(chat_id, user_id, metadata.get("message_id") or "", text, intent)
        return intent

    async def _classify(
        self, key, user_id, text, has_image=False, prev_text="", last_image=False, voice_on=False, learned=()
    ) -> tuple[dict, float]:
        """One call to CLASSIFIER_MODEL: the turn's formats (read with read_intent) and what the call cost."""
        system = CLASSIFIER_PROMPT
        if learned:
            system += "\n\nThis user's own earlier messages that were first routed wrongly, with the right formats:\n"
            system += "\n".join(f"{json.dumps(t, ensure_ascii=False)} → {describe(labels)}" for t, labels in learned)
        lines = []
        if prev_text:
            lines.append(f"Previous user message: {excerpt(prev_text, TEXT_LIMIT)}")
            lines.append(f"Previous reply was: {'an image' if last_image else 'text'}")
        lines.append(f"Voice mode in this chat: {'on' if voice_on else 'off'}")
        lines.append(f"Message{' (with images attached)' if has_image else ''}: {excerpt(text, MESSAGE_LIMIT)}")
        payload = {
            "models": [self.valves.CLASSIFIER_MODEL],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}],
            "stream": True,
            "usage": {"include": True},
            "temperature": 0,
            "max_tokens": 500,  # unlike chat (CR-1 D13) there's no thinking to cut: reasoning is off
            "reasoning": {"enabled": False},
            "response_format": INTENT_SCHEMA,
            # only providers that honour the schema
            "provider": {"allow_fallbacks": True, "require_parameters": True},
        }
        if user_id:
            payload["user"] = user_id
        reply, cost = await self._complete(payload, key, privacy=True)
        raw = json.loads(reply[reply.find("{") : reply.rfind("}") + 1])
        return read_intent(raw, voice_on, bool(prev_text)), cost

    async def _record(self, chat_id, user_id, message_id, text, intent) -> None:
        """Voice mode on or off, misses on the previous turn, and this turn as the chat's last (CR-4 §3.7, D16)."""
        prev = self._remember("last_turn", chat_id)
        if prev and prev["user_id"] == user_id:
            corrected = intent["corrects"]
            if corrected and corrected != prev["labels"]:
                self._remember("add_miss", user_id, chat_id, prev["message_id"], prev["text"], corrected, "correction", True)
            if (
                prev["message_id"]
                and await self._rated_down(user_id, prev["message_id"])
                and not self._remember("has_miss", prev["message_id"], "rating", default=True)
            ):
                self._remember("add_miss", user_id, chat_id, prev["message_id"], prev["text"], prev["labels"], "rating", False)
        if intent["voice"] in ("on", "off"):
            self._remember("set_voice", chat_id, intent["voice"] == "on")
        self._remember("set_last_turn", chat_id, user_id, message_id, text, labels_of(intent))

    @staticmethod
    async def _rated_down(user_id: str, message_id: str) -> bool:
        """Whether the user gave this reply a thumbs-down, from Open WebUI's feedback (CR-4 O7)."""
        try:
            from open_webui.models.feedbacks import Feedbacks

            feedback = await Feedbacks.get_feedbacks_by_user_id(user_id, limit=5)
            return any(
                (f.meta or {}).get("message_id") == message_id and str((f.data or {}).get("rating")) == "-1"
                for f in feedback.items
            )
        except Exception:  # outside Open WebUI (tests) or an API change: no weak signal
            return False

    def _remember(self, action: str, *args, default=None):
        """Calls the store. It's a help, not a need: a failure is logged and the reply goes on."""
        try:
            if not self._store or self._store.path != self.valves.STORE_PATH:
                self._store = Store(self.valves.STORE_PATH)
            return getattr(self._store, action)(*args)
        except Exception as e:
            log.warning("router: store %s failed: %s", action, e)
            return default

    # ---------- chat ----------

    async def _chat(self, body, chain_id, key, user_id, intent, emitter, ask=None) -> AsyncGenerator[str, None]:
        messages = body.get("messages", [])
        models = split_models(getattr(self.valves, CHAINS[chain_id][1]))
        if has_images(messages):
            models = await self._vision_chain(models)

        # Paid chain first, then the free chain as the last resort (R5); skip the repeat when they're the same.
        plan = [("chain", models)]
        free = split_models(self.valves.FREE_MODELS)
        if chain_id != "free":
            plan.append(("free", await self._vision_chain(free) if has_images(messages) else free))

        last_error: Optional[RouteError] = None
        for label, chain in plan:
            for batch in batches(chain):
                for data_collection in ("deny", "allow"):
                    payload = self._payload(body, batch, user_id, data_collection, intent["web"])
                    started = False
                    try:
                        async for piece, info in self._attempt(payload, key, ask):
                            if piece:
                                started = True
                                yield piece
                            elif info is not None:
                                await self._footer(emitter, info, label, data_collection, intent)
                                if info["stopped"]:
                                    yield f"\n\n_{FRIENDLY['stopped'].format(minutes=minutes(info['stopped']))}_"
                                elif info["finish_reason"] == "length":
                                    # Never end silently on a cut reply, least of all one that is only thinking.
                                    yield f"\n\n_{FRIENDLY['cut' if info['answered'] else 'cut_thinking']}_"
                        return
                    except RouteError as e:
                        last_error = e
                        log.warning("router: %s %s, data_collection=%s: %s", label, batch, data_collection, e)
                        if started:
                            # Part of a reply is already on screen: say it was cut instead of starting over.
                            yield f"\n\n_{FRIENDLY.get(e.kind, FRIENDLY['other'])}_"
                            return
                        if e.kind == "auth":
                            yield FRIENDLY["auth"]
                            return
                        if e.kind in ("credits", "budget"):
                            break  # paid routes can't answer: go straight to the free chain
                        # Anything else (no private provider, busy, an outage): allowing logging
                        # providers widens the pool, then the next batch of the chain gets a turn.
                else:
                    continue
                break  # credits or budget: skip the rest of this chain
        yield FRIENDLY.get(last_error.kind if last_error else "other", FRIENDLY["other"])

    def _payload(self, body, models, user_id, data_collection, web) -> dict:
        payload = {
            "models": models,
            "messages": body.get("messages", []),
            "stream": True,
            "usage": {"include": True},
            "provider": {"data_collection": data_collection, "allow_fallbacks": True},
        }
        # No max_tokens of our own: thinking counts against it, and a cap left thinking models without an answer.
        # The thinking time limit (_stream) and the monthly budget bound the cost instead (CR-1 D13).
        for name in ("max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty", "stop", "seed"):
            if body.get(name) is not None:
                payload[name] = body[name]
        if user_id:
            payload["user"] = user_id
        if web:
            payload["plugins"] = [
                {"id": "web", "engine": self.valves.WEB_ENGINE, "max_results": self.valves.WEB_MAX_RESULTS}
            ]
        return payload

    async def _attempt(self, payload: dict, key: str, ask=None):
        """_stream, asked once more with a smaller max_tokens when low credits can't cover the model's full output."""
        streamed = False
        try:
            async for item in self._stream(payload, key, ask):
                streamed = True
                yield item
        except RouteError as e:
            match = AFFORD.search(e.detail)
            afford = int(match.group(1)) if match else 0
            if streamed or e.kind != "credits" or afford < MIN_AFFORD or afford >= payload.get("max_tokens", afford + 1):
                raise
            log.info("router: low credits, asking again with max_tokens=%d", afford)
            async for item in self._stream({**payload, "max_tokens": afford}, key, ask):
                yield item

    async def _stream(self, payload: dict, key: str, ask=None):
        """Yields (text, None) for each piece of the reply, then (None, info) with model, tokens and cost, why the
        reply ended, whether any answer (not just thinking) came, and how long it had thought if the user stopped it.

        With `ask` (Open WebUI's __event_call__), a model that has thought for THINKING_LIMIT_SECONDS without answering
        makes a dialog ask the user to keep waiting or stop. The reply keeps streaming while the dialog is open, and it
        closes by itself once the answer starts."""
        info = {"model": "", "tokens": 0, "cost": 0.0, "finish_reason": None, "answered": False, "stopped": 0.0}
        in_reasoning = False
        limit = self.valves.THINKING_LIMIT_SECONDS if ask else 0
        started_at = time.monotonic()
        deadline = started_at + limit if limit > 0 else None
        question: Optional[asyncio.Task] = None
        pump: Optional[asyncio.Task] = None
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", f"{self.valves.API_BASE_URL}/chat/completions", headers=self._headers(key), json=payload
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(errors="replace")
                        raise RouteError(classify_error(response.status_code, detail), detail, response.status_code)
                    lines: asyncio.Queue = asyncio.Queue()
                    pump = asyncio.create_task(self._pump(response, lines))
                    while True:
                        if question and question.done():
                            answer = None if question.cancelled() or question.exception() else question.result()
                            question = None
                            if answer is False:  # "Cancel" in the dialog: stop thinking
                                info["stopped"] = time.monotonic() - started_at
                                break
                            deadline = time.monotonic() + limit  # kept waiting (or the tab went away): ask again later
                        wait = None
                        if question:
                            wait = 1.0  # look at the dialog again every second
                        elif deadline and not info["answered"]:
                            wait = max(deadline - time.monotonic(), 0)
                        try:
                            line = await asyncio.wait_for(lines.get(), wait)
                        except asyncio.TimeoutError:
                            if not question and not info["answered"]:
                                question = asyncio.create_task(self._ask_to_wait(ask, time.monotonic() - started_at))
                            continue
                        if isinstance(line, Exception):
                            raise line
                        if line is None:
                            break
                        if not line.startswith("data:"):
                            continue  # blank lines and ": OPENROUTER PROCESSING" keep-alives
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if "error" in chunk:
                            error = chunk["error"]
                            detail = error.get("message", "") if isinstance(error, dict) else str(error)
                            code = error.get("code", 0) if isinstance(error, dict) else 0
                            raise RouteError(classify_error(int(code or 0), detail), detail, int(code or 0))
                        info["model"] = chunk.get("model") or info["model"]
                        usage = chunk.get("usage")
                        if usage:
                            info["tokens"] = usage.get("total_tokens", 0)
                            info["cost"] = float(usage.get("cost") or 0)
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta", {})
                            reasoning = delta.get("reasoning")
                            if reasoning:
                                if not in_reasoning:
                                    in_reasoning = True
                                    yield "<think>\n", None
                                yield reasoning, None
                            content = delta.get("content")
                            if content:
                                if in_reasoning:
                                    in_reasoning = False
                                    yield "\n</think>\n\n", None
                                info["answered"] = True
                                if question:
                                    question.cancel()  # the answer started: Open WebUI closes the dialog
                                yield content, None
                            info["finish_reason"] = choice.get("finish_reason") or info["finish_reason"]
        except httpx.HTTPError as e:
            raise RouteError("network", str(e)) from e
        finally:
            for task in (question, pump):
                if task:
                    task.cancel()
        if in_reasoning:
            yield "\n</think>\n\n", None
        yield None, info

    @staticmethod
    async def _pump(response: httpx.Response, lines: asyncio.Queue) -> None:
        """Reads the stream into `lines`, ending with None, or with the error that broke it."""
        try:
            async for line in response.aiter_lines():
                await lines.put(line)
        except Exception as e:
            await lines.put(e)
            return
        await lines.put(None)

    @staticmethod
    async def _ask_to_wait(ask, elapsed: float):
        """True to keep waiting, False to stop; anything else (tab closed, no answer) keeps the model going."""
        return await ask(
            {
                "type": "confirmation",
                "data": {
                    "title": "Still thinking",
                    "message": f"The model has been thinking for {minutes(elapsed)} and hasn't started the answer. "
                    "Confirm to keep waiting, or cancel to stop it.",
                },
            }
        )

    async def _footer(self, emitter, info, label, data_collection, intent):
        if not (emitter and self.valves.SHOW_FOOTER):
            return
        cost = info["cost"] + intent["cost"]  # the classifier's call counts towards the reply
        parts = [info["model"] or "unknown model", f"{info['tokens']:,} tokens", f"${cost:.4f}"]
        if label == "free":
            parts.append("free fallback")
        if data_collection == "allow":
            parts.append("logging provider")
        if intent["formats"]:
            parts.append("auto: " + ", ".join(intent["formats"]))
        elif intent["web"]:
            parts.append("web search")
        await emitter({"type": "status", "data": {"description": " · ".join(parts), "done": True}})

    async def _complete(self, payload: dict, key: str, privacy: bool) -> tuple[str, float]:
        """A short call whose whole reply is wanted at once (titles and tags, the classifier): the reply without its
        thinking, and its cost. With `privacy`, non-logging providers first, as for chat."""
        last_error = RouteError("other")
        for data_collection in ("deny", "allow") if privacy else ("allow",):
            attempt = {**payload, "provider": {**payload.get("provider", {}), "data_collection": data_collection}}
            text, cost = [], 0.0
            try:
                async for piece, info in self._stream(attempt, key):
                    if piece:
                        text.append(piece)
                    elif info is not None:
                        cost = info["cost"]
            except RouteError as e:
                last_error = e
                if e.kind in ("auth", "credits", "budget"):
                    break
                continue
            return "".join(text).split("</think>")[-1].strip(), cost
        raise last_error

    # ---------- titles, tags, follow-ups ----------

    async def _task(self, body, key, user_id) -> str:
        try:
            reply, _ = await self._complete(
                self._payload(body, [self.valves.TASK_MODEL], user_id, "allow", False), key, privacy=False
            )
        except RouteError as e:
            log.warning("router: task failed: %s", e)
            return ""
        return reply

    # ---------- image generation ----------

    async def _image(self, body, key, user_id, intent, emitter, request, metadata, user) -> AsyncGenerator[str, None]:
        prompt = intent["image_prompt"] or last_user_text(body.get("messages", [])).strip()
        if not prompt:
            yield "Describe the image you want in your message."
            return
        last_error: Optional[RouteError] = None
        for model in split_models(self.valves.IMAGE_MODELS):
            try:
                image, cost = await self._generate_image(model, prompt, key, user_id)
            except RouteError as e:
                last_error = e
                log.warning("router: image model %s: %s", model, e)
                if e.kind in ("auth", "credits", "budget"):
                    break
                continue
            url = await self._store_image(image, request, metadata, user)
            yield f"![{alt_text(prompt)}]({url})"
            await self._footer(emitter, {"model": model, "tokens": 0, "cost": cost}, "chain", "n/a", intent)
            return
        yield FRIENDLY.get(last_error.kind if last_error else "other", FRIENDLY["other"])

    async def _generate_image(self, model, prompt, key, user_id) -> tuple[dict, float]:
        payload = {"model": model, "prompt": prompt, "n": 1, "aspect_ratio": "1:1", "output_format": "webp"}
        if user_id:
            payload["user"] = user_id
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.valves.API_BASE_URL}/images", headers=self._headers(key), json=payload
                )
        except httpx.HTTPError as e:
            raise RouteError("network", str(e)) from e
        if response.status_code != 200:
            raise RouteError(classify_error(response.status_code, response.text), response.text, response.status_code)
        result = response.json()
        images = result.get("data") or []
        if not images or not images[0].get("b64_json"):
            raise RouteError("other", "no image in the response")
        return images[0], float((result.get("usage") or {}).get("cost") or 0)

    async def _store_image(self, image: dict, request, metadata, user) -> str:
        data_url = f"data:{image.get('media_type') or 'image/png'};base64,{image['b64_json']}"
        try:
            # Store it as an Open WebUI file, so the chat keeps a link instead of megabytes of base64.
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_url_from_base64

            user_model = await Users.get_user_by_id(user["id"])
            return await get_image_url_from_base64(request, data_url, metadata or {}, user_model) or data_url
        except Exception as e:  # outside Open WebUI (tests) or an API change: the data URL still works
            log.warning("router: storing the image as a file failed, keeping it inline: %s", e)
            return data_url

    # ---------- helpers ----------

    async def _vision_chain(self, models: list[str]) -> list[str]:
        vision = await self._vision_model_ids()
        if not vision:
            return models  # couldn't check: let OpenRouter decide
        chosen = [m for m in models if m in vision]
        return chosen or [m for m in split_models(self.valves.VISION_MODELS) if m in vision] or models

    async def _vision_model_ids(self) -> set[str]:
        if self._vision_ids and time.time() - self._vision_fetched_at < 3600:
            return self._vision_ids
        try:
            async with self._client() as client:
                response = await client.get(f"{self.valves.API_BASE_URL}/models")
            response.raise_for_status()
            self._vision_ids = {
                m["id"]
                for m in response.json().get("data", [])
                if "image" in (m.get("architecture") or {}).get("input_modalities", [])
            }
            self._vision_fetched_at = time.time()
        except Exception as e:
            log.warning("router: model list unavailable: %s", e)
        return self._vision_ids

    def _read_key(self) -> str:
        try:
            with open(self.valves.API_KEY_FILE, encoding="utf-8") as f:
                key = f.read().strip()
        except OSError as e:
            raise RouteError("key_missing", str(e)) from e
        if not key or key.startswith("<") or key.upper().startswith("REPLACE"):
            raise RouteError("key_missing", "placeholder")
        return key

    def _headers(self, key: str) -> dict:
        return {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://openai.selvalogesh.com",
            "X-Title": "personal-llm-assistant",
        }

    @asynccontextmanager
    async def _client(self):
        """One client kept open, so calls reuse the connection: a new one cost about 0.7 s a call (CR-4 O5). It's
        made again when the event loop, the transport (tests) or TIMEOUT_SECONDS changes."""
        key = (asyncio.get_running_loop(), self.transport, self.valves.TIMEOUT_SECONDS)
        if self._http is None or self._http.is_closed or self._http_key != key:
            self._http = httpx.AsyncClient(transport=self.transport, timeout=self.valves.TIMEOUT_SECONDS)
            self._http_key = key
        yield self._http
