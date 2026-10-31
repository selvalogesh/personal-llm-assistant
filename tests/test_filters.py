import base64
import json

import httpx

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


def voice_filter(handler):
    f = voice.Filter()
    f.transport = httpx.MockTransport(handler)
    return f


def test_reply_gets_a_voice_note():
    sent = []

    def kokoro(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=b"\xff" * 16000)  # 1 s at 128 kbit/s

    emitter = Emitter()
    body = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello **there**."}]}
    assert run(voice_filter(kokoro).outlet(body, __event_emitter__=emitter)) is body
    assert sent[0]["input"] == "Hello there."
    assert sent[0]["voice"] == "af_heart" and sent[0]["stream"] is False
    embed = next(e for e in emitter.events if e["type"] == "embeds")["data"]["embeds"][0]
    assert "data:audio/mpeg;base64," + base64.b64encode(b"\xff" * 16000).decode() in embed
    assert "0:01" in embed
    assert emitter.statuses() == ["Recording voice note…", "Voice note ready"]


def test_kokoro_down_leaves_the_reply_alone():
    emitter = Emitter()
    body = {"messages": [{"role": "assistant", "content": "Hello."}]}
    result = run(voice_filter(lambda r: httpx.Response(503)).outlet(body, __event_emitter__=emitter))
    assert result is body
    assert emitter.statuses()[-1] == "Voice note unavailable right now"
    assert not any(e["type"] == "embeds" for e in emitter.events)


def test_nothing_to_say_makes_no_call():
    def kokoro(request):
        raise AssertionError("Kokoro shouldn't be called")

    f = voice_filter(kokoro)
    emitter = Emitter()
    run(f.outlet({"messages": [{"role": "user", "content": "hi"}]}, __event_emitter__=emitter))
    run(f.outlet({"messages": [{"role": "assistant", "content": "```code only```"}]}, __event_emitter__=emitter))
    assert emitter.events == []
