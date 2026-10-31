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


def delta(content=None, reasoning=None, model="vendor/model") -> dict:
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning"] = reasoning
    return {"model": model, "choices": [{"delta": d}]}


def usage(model="vendor/model", tokens=42, cost=0.0012) -> dict:
    return {"model": model, "choices": [], "usage": {"total_tokens": tokens, "cost": cost}}


class FakeOpenRouter:
    """Answers each chat request from a queue of (status, body) and records every request payload."""

    def __init__(self, responses=None, models=None, images=None):
        self.responses = list(responses or [])
        self.models = models or []
        self.images = list(images or [])
        self.requests: list[dict] = []
        self.image_requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": self.models})
        payload = json.loads(request.content)
        if request.url.path.endswith("/images"):
            self.image_requests.append(payload)
            status, body = self.images.pop(0)
            return httpx.Response(status, json=body)
        self.requests.append(payload)
        status, body = self.responses.pop(0) if self.responses else (429, {"error": {"message": "busy"}})
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
