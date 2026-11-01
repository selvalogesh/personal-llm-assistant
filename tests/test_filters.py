import base64
import json

import httpx
import pytest

from conftest import Emitter, load, run

voice = load("functions/voice_note.py")
web = load("functions/web_search.py")
image = load("functions/image_mode.py")
router = load("functions/router.py")


def test_toggles_set_the_routers_flags():
    for module, flag in ((web, router.WEB_FLAG), (image, router.IMAGE_FLAG)):
        f = module.Filter()
        assert f.toggle is True and f.icon.startswith("data:image/svg+xml")
        assert run(f.inlet({"messages": []}))[flag] is True


def test_speakable_keeps_only_prose():
    text = "<think>plan</think>Here is **bold** and [a link](http://x).\n```py\nprint(1)\n```\n![img](u) Done."
    assert voice.speakable(text) == "Here is bold and a link. (code omitted) Done."


def test_shorten_cuts_at_a_sentence():
    text = "One sentence here. Another one follows. " * 10
    short = voice.shorten(text, 60)
    assert short == "One sentence here. Another one follows. One sentence here. The rest is in the text."
    assert voice.shorten("tiny", 60) == "tiny"


@pytest.fixture
def store_path(tmp_path):
    """The Router's store with reply-1 marked for a voice note, as the Router writes it."""
    path = tmp_path / "router" / "router.sqlite"
    router.Store(str(path)).mark_voice("reply-1")
    return path


def voice_filter(handler, store_path):
    f = voice.Filter()
    f.valves = f.Valves(STORE_PATH=str(store_path))
    f.transport = httpx.MockTransport(handler)
    return f


def test_voice_filter_has_no_toggle():
    # It runs after every reply; the Router decides which ones (CR-4 §3.5)
    assert not hasattr(voice.Filter(), "toggle")


def test_reply_gets_a_voice_note(store_path):
    sent = []

    def kokoro(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=b"\xff" * 16000)  # 1 s at 128 kbit/s

    emitter = Emitter()
    body = {
        "id": "reply-1",
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello **there**."}],
    }
    assert run(voice_filter(kokoro, store_path).outlet(body, __event_emitter__=emitter)) is body
    assert sent[0]["input"] == "Hello there."
    assert sent[0]["voice"] == "af_heart" and sent[0]["stream"] is False
    embed = next(e for e in emitter.events if e["type"] == "embeds")["data"]["embeds"][0]
    assert "data:audio/mpeg;base64," + base64.b64encode(b"\xff" * 16000).decode() in embed
    assert "0:01" in embed
    assert emitter.statuses() == ["Recording voice note…", "Voice note ready"]


def test_kokoro_down_leaves_the_reply_alone(store_path):
    emitter = Emitter()
    body = {"id": "reply-1", "messages": [{"role": "assistant", "content": "Hello."}]}
    result = run(voice_filter(lambda r: httpx.Response(503), store_path).outlet(body, __event_emitter__=emitter))
    assert result is body
    assert emitter.statuses()[-1] == "Voice note unavailable right now"
    assert not any(e["type"] == "embeds" for e in emitter.events)


def no_kokoro(request):
    raise AssertionError("Kokoro shouldn't be called")


def test_nothing_to_say_makes_no_call(store_path):
    f = voice_filter(no_kokoro, store_path)
    emitter = Emitter()
    run(f.outlet({"id": "reply-1", "messages": [{"role": "user", "content": "hi"}]}, __event_emitter__=emitter))
    code_only = {"id": "reply-1", "messages": [{"role": "assistant", "content": "```code only```"}]}
    run(f.outlet(code_only, __event_emitter__=emitter))
    assert emitter.events == []


def test_unmarked_reply_or_no_store_gets_no_voice_note(store_path, tmp_path):
    emitter = Emitter()
    body = {"id": "reply-2", "messages": [{"role": "assistant", "content": "Hello."}]}
    run(voice_filter(no_kokoro, store_path).outlet(body, __event_emitter__=emitter))
    run(voice_filter(no_kokoro, tmp_path / "missing.sqlite").outlet({**body, "id": "reply-1"}, __event_emitter__=emitter))
    assert emitter.events == []
    assert not (tmp_path / "missing.sqlite").exists()  # reading never creates the Router's file
