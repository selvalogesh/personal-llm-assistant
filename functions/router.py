"""
title: Router
author: selvalogesh
version: 1.1.0
description: Sends each chat turn to OpenRouter through a chain of models. Non-logging providers first, the free chain when credits or paid routes fail, and a plain message instead of a raw error (CR-1 §3.1).
"""

import asyncio
import json
import logging
import re
import time
from typing import AsyncGenerator, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger("router")

# Flags the toggle filters (web_search.py, image_mode.py) set on the request body.
WEB_FLAG = "router_web_search"
IMAGE_FLAG = "router_image_mode"

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


def last_user_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    return ""


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

        if body.pop(IMAGE_FLAG, False):
            return self._image(body, key, user_id, __event_emitter__, __request__, __metadata__, __user__)

        web = bool(body.pop(WEB_FLAG, False))
        return self._chat(body, chain_id, key, user_id, web, __event_emitter__, __event_call__)

    # ---------- chat ----------

    async def _chat(self, body, chain_id, key, user_id, web, emitter, ask=None) -> AsyncGenerator[str, None]:
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
                    payload = self._payload(body, batch, user_id, data_collection, web)
                    started = False
                    try:
                        async for piece, info in self._attempt(payload, key, ask):
                            if piece:
                                started = True
                                yield piece
                            elif info is not None:
                                await self._footer(emitter, info, label, data_collection, web)
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

    async def _footer(self, emitter, info, label, data_collection, web):
        if not (emitter and self.valves.SHOW_FOOTER):
            return
        parts = [info["model"] or "unknown model", f"{info['tokens']:,} tokens", f"${info['cost']:.4f}"]
        if label == "free":
            parts.append("free fallback")
        if data_collection == "allow":
            parts.append("logging provider")
        if web:
            parts.append("web search")
        await emitter({"type": "status", "data": {"description": " · ".join(parts), "done": True}})

    # ---------- titles, tags, follow-ups ----------

    async def _task(self, body, key, user_id) -> str:
        payload = self._payload(body, [self.valves.TASK_MODEL], user_id, "allow", False)
        text = []
        try:
            async for piece, _ in self._stream(payload, key):
                if piece:
                    text.append(piece)
        except RouteError as e:
            log.warning("router: task failed: %s", e)
            return ""
        reply = "".join(text)
        return reply.split("</think>")[-1].strip()

    # ---------- image generation ----------

    async def _image(self, body, key, user_id, emitter, request, metadata, user) -> AsyncGenerator[str, None]:
        prompt = last_user_text(body.get("messages", [])).strip()
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
            yield f"![{prompt[:80]}]({url})"
            await self._footer(emitter, {"model": model, "tokens": 0, "cost": cost}, "chain", "n/a", False)
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

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=self.valves.TIMEOUT_SECONDS)
