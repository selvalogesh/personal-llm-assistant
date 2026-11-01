"""Loads the single-file Open WebUI functions as modules and fakes OpenRouter and Kokoro with httpx.MockTransport."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(coro):
    return asyncio.run(coro)


async def collect(result) -> str:
    """A pipe returns a string or an async generator of strings."""
    if isinstance(result, str):
        return result
    return "".join([piece async for piece in result])


def sse(*chunks: dict, done: bool = True) -> bytes:
    lines = [": OPENROUTER PROCESSING", ""]
    for chunk in chunks:
        lines += [f"data: {json.dumps(chunk)}", ""]
    if done:
        lines += ["data: [DONE]", ""]
    return "\n".join(lines).encode()


def delta(content=None, reasoning=None, model="vendor/model", finish=None) -> dict:
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning"] = reasoning
    return {"model": model, "choices": [{"delta": d, "finish_reason": finish}]}


def usage(model="vendor/model", tokens=42, cost=0.0012, searches=0) -> dict:
    counts = {"total_tokens": tokens, "cost": cost}
    if searches:
        counts["server_tool_use_details"] = {"web_search_requests": searches}
    return {"model": model, "choices": [], "usage": counts}


def tool_call(name, args, call_id="call-1", index=0, model="vendor/model") -> dict:
    """A tool call as OpenRouter streams it, ending the reply with finish_reason "tool_calls" (CR-4 O4)."""
    piece = {"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return {"model": model, "choices": [{"delta": {"tool_calls": [piece]}, "finish_reason": "tool_calls"}]}


class Slow(httpx.AsyncByteStream):
    """An event-stream body that waits `pause` seconds before each event, for the thinking time limit."""

    def __init__(self, *chunks: dict, pause: float):
        self.events = [f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks] + [b"data: [DONE]\n\n"]
        self.pause = pause

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(self.pause)
            yield event


class Asker:
    """Open WebUI's __event_call__: answers each dialog from `answers` (None waits until cancelled)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions: list[dict] = []
        self.cancelled = 0

    async def __call__(self, event: dict):
        self.questions.append(event)
        answer = self.answers.pop(0) if self.answers else True
        if answer is None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return answer


def intent(cost=0.0, **fields) -> tuple:
    """The classifier's streamed answer: plain chat with `fields` changed."""
    answer = {"image": False, "image_prompt": "", "web": False, "code": False, "voice": "none", "corrects_previous": None}
    return 200, sse(delta(json.dumps({**answer, **fields})), usage(tokens=0, cost=cost))


class FakeOpenRouter:
    """Answers each chat request from a queue of (status, body) and records every request payload. The classifier's
    calls (they carry `response_format`) are answered from `intents` instead, plain chat when it runs out."""

    def __init__(self, responses=None, models=None, images=None, intents=None):
        self.responses = list(responses or [])
        self.models = models or []
        self.images = list(images or [])
        self.intents = list(intents or [])
        self.requests: list[dict] = []
        self.image_requests: list[dict] = []
        self.classify_requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": self.models})
        payload = json.loads(request.content)
        if request.url.path.endswith("/images"):
            self.image_requests.append(payload)
            status, body = self.images.pop(0)
            return httpx.Response(status, json=body)
        if "response_format" in payload:
            self.classify_requests.append(payload)
            status, body = self.intents.pop(0) if self.intents else intent()
        else:
            self.requests.append(payload)
            status, body = self.responses.pop(0) if self.responses else (429, {"error": {"message": "busy"}})
        if isinstance(body, Slow):
            return httpx.Response(status, stream=body, headers={"content-type": "text/event-stream"})
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(status, json=body)

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)


class Emitter:
    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, event: dict):
        self.events.append(event)

    def statuses(self) -> list[str]:
        return [e["data"]["description"] for e in self.events if e["type"] == "status"]


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "openrouter-api-key"
    path.write_text("sk-or-v1-test\n")
    return path
