import base64
import json
import sqlite3
import sys
import types

from conftest import (
    ROOT, Asker, Emitter, FakeOpenRouter, Slow, collect, delta, intent, load, run, sse, tool_call, usage,
)

router = load("functions/router.py")


def make_pipe(fake: FakeOpenRouter, key_file, **valves):
    pipe = router.Pipe()
    valves.setdefault("STORE_PATH", str(key_file.parent / "store" / "router.sqlite"))
    pipe.valves = pipe.Valves(API_KEY_FILE=str(key_file), **valves)
    pipe.transport = fake.transport
    return pipe


def chat(pipe, model="router.auto", content="hi", emitter=None, task=None, ask=None, messages=None, user="user-1",
         metadata=None, **extra):
    messages = (messages or []) + [{"role": "user", "content": content}]
    body = {"model": model, "messages": messages, **extra}
    return run(collect_pipe(pipe, body, emitter, task, ask, user, metadata))


async def collect_pipe(pipe, body, emitter, task, ask=None, user="user-1", metadata=None):
    result = await pipe.pipe(
        body, __user__={"id": user}, __metadata__=metadata, __event_emitter__=emitter, __event_call__=ask,
        __task__=task,
    )
    return await collect(result)


OK = (200, sse(delta("Hello"), delta(" there"), usage()))


def test_pipes_lists_the_four_options():
    assert [p["name"] for p in router.Pipe().pipes()] == ["Auto", "Creative", "Smart", "Free"]


def test_streams_the_reply_and_shows_a_footer(key_file):
    fake = FakeOpenRouter([OK])
    emitter = Emitter()
    assert chat(make_pipe(fake, key_file), emitter=emitter) == "Hello there"
    assert emitter.statuses() == ["vendor/model · 42 tokens · $0.0012"]


def test_reasoning_is_wrapped_in_think_tags(key_file):
    fake = FakeOpenRouter([(200, sse(delta(reasoning="Let me see."), delta("Answer"), usage()))])
    assert chat(make_pipe(fake, key_file)) == "<think>\nLet me see.\n</think>\n\nAnswer"


def test_reply_cut_while_thinking_says_there_is_no_answer(key_file):
    # A length limit ran out mid-thought (seen on the server with DeepSeek V4.1 Flash and the old 4,096 cap)
    fake = FakeOpenRouter([(200, sse(delta(reasoning="Hmm, first"), delta(reasoning=" the arms", finish="length"), usage()))])
    reply = chat(make_pipe(fake, key_file))
    assert reply == f"<think>\nHmm, first the arms\n</think>\n\n\n\n_{router.FRIENDLY['cut_thinking']}_"


def test_reply_cut_after_answering_says_it_stops_there(key_file):
    fake = FakeOpenRouter([(200, sse(delta(reasoning="Plan."), delta("Part one", finish="length"), usage()))])
    assert chat(make_pipe(fake, key_file)).endswith(f"Part one\n\n_{router.FRIENDLY['cut']}_")


def test_finished_reply_gets_no_note(key_file):
    fake = FakeOpenRouter([(200, sse(delta("Done", finish="stop"), usage()))])
    assert chat(make_pipe(fake, key_file)) == "Done"


THINKING = [delta(reasoning=f"step {i}. ") for i in range(8)]


def test_long_thinking_asks_and_stops_when_told(key_file):
    fake = FakeOpenRouter([(200, Slow(*THINKING, delta("Answer"), usage(), pause=0.05))])
    ask = Asker(False)
    reply = chat(make_pipe(fake, key_file, THINKING_LIMIT_SECONDS=0.12), ask=ask)
    assert len(ask.questions) == 1
    assert ask.questions[0]["type"] == "confirmation"
    assert "Answer" not in reply
    assert reply.endswith(f"</think>\n\n\n\n_{router.FRIENDLY['stopped'].format(minutes='1 minute')}_")


def test_long_thinking_keeps_going_when_told_and_asks_again(key_file):
    fake = FakeOpenRouter([(200, Slow(*THINKING, delta("Answer"), usage(), pause=0.05))])
    ask = Asker(True, True, True, True, True)
    reply = chat(make_pipe(fake, key_file, THINKING_LIMIT_SECONDS=0.12), ask=ask)
    assert reply.endswith("Answer")
    assert len(ask.questions) >= 2


def test_dialog_closes_by_itself_when_the_answer_starts(key_file):
    fake = FakeOpenRouter([(200, Slow(*THINKING, delta("Answer"), usage(), pause=0.05))])
    ask = Asker(None)  # nobody answers the dialog
    reply = chat(make_pipe(fake, key_file, THINKING_LIMIT_SECONDS=0.2), ask=ask)
    assert reply.endswith("Answer")
    assert len(ask.questions) == 1 and ask.cancelled == 1


def test_quick_answers_never_ask(key_file):
    ask = Asker(False)
    assert chat(make_pipe(FakeOpenRouter([OK]), key_file), ask=ask) == "Hello there"
    assert ask.questions == []


def test_low_credits_ask_again_with_what_they_cover(key_file):
    afford = (402, {"error": {"message": "You requested up to 65536 tokens, but can only afford 3000."}})
    fake = FakeOpenRouter([afford, OK])
    emitter = Emitter()
    assert chat(make_pipe(fake, key_file), emitter=emitter) == "Hello there"
    assert "max_tokens" not in fake.requests[0]
    assert fake.requests[1]["max_tokens"] == 3000
    assert fake.requests[1]["models"] == fake.requests[0]["models"]
    assert "free fallback" not in emitter.statuses()[0]


def test_almost_no_credits_go_to_the_free_chain(key_file):
    afford = (402, {"error": {"message": "You requested up to 65536 tokens, but can only afford 120."}})
    fake = FakeOpenRouter([afford, OK])
    pipe = make_pipe(fake, key_file, AUTO_MODELS="a", FREE_MODELS="f1")
    assert chat(pipe) == "Hello there"
    assert [r["models"] for r in fake.requests] == [["a"], ["f1"]]


def test_first_request_denies_logging_and_carries_user(key_file):
    fake = FakeOpenRouter([OK])
    chat(make_pipe(fake, key_file), temperature=0.3)
    payload = fake.requests[0]
    assert payload["provider"] == {"data_collection": "deny", "allow_fallbacks": True}
    assert payload["user"] == "user-1"
    assert "max_tokens" not in payload  # thinking counts against a cap (CR-1 D13)
    assert payload["temperature"] == 0.3
    assert payload["usage"] == {"include": True}
    assert "plugins" not in payload


def test_chains_go_in_groups_of_three(key_file):
    fake = FakeOpenRouter([(429, {"error": {"message": "busy"}}), (429, {"error": {"message": "busy"}}), OK])
    pipe = make_pipe(fake, key_file, AUTO_MODELS="a, b, c, d")
    assert chat(pipe) == "Hello there"
    assert [r["models"] for r in fake.requests] == [["a", "b", "c"], ["a", "b", "c"], ["d"]]
    assert [r["provider"]["data_collection"] for r in fake.requests] == ["deny", "allow", "deny"]


def test_no_private_provider_retries_with_logging_allowed(key_file):
    no_endpoints = (404, {"error": {"message": "No endpoints found matching your data policy"}})
    fake = FakeOpenRouter([no_endpoints, OK])
    emitter = Emitter()
    assert chat(make_pipe(fake, key_file), emitter=emitter) == "Hello there"
    assert fake.requests[1]["provider"]["data_collection"] == "allow"
    assert emitter.statuses()[0].endswith("logging provider")


def test_out_of_credits_skips_to_the_free_chain(key_file):
    fake = FakeOpenRouter([(402, {"error": {"message": "Insufficient credits"}}), OK])
    emitter = Emitter()
    pipe = make_pipe(fake, key_file, AUTO_MODELS="a, b, c, d", FREE_MODELS="f1, f2")
    assert chat(pipe, emitter=emitter) == "Hello there"
    assert [r["models"] for r in fake.requests] == [["a", "b", "c"], ["f1", "f2"]]
    assert "free fallback" in emitter.statuses()[0]


def test_budget_used_up_skips_to_the_free_chain(key_file):
    fake = FakeOpenRouter([(403, {"error": {"message": "Workspace budget limit exceeded"}}), OK])
    pipe = make_pipe(fake, key_file, AUTO_MODELS="a", FREE_MODELS="f1")
    assert chat(pipe) == "Hello there"
    assert fake.requests[1]["models"] == ["f1"]


def test_rejected_key_stops_at_once(key_file):
    fake = FakeOpenRouter([(401, {"error": {"message": "No auth credentials found"}})])
    assert chat(make_pipe(fake, key_file)) == router.FRIENDLY["auth"]
    assert len(fake.requests) == 1


def test_everything_failing_gives_a_plain_message(key_file):
    fake = FakeOpenRouter([])  # every request: 429
    pipe = make_pipe(fake, key_file, AUTO_MODELS="a, b, c, d", FREE_MODELS="f1")
    assert chat(pipe) == router.FRIENDLY["busy"]
    # groups (a b c) and (d), then the free chain, each denied then allowed
    assert len(fake.requests) == 6


def test_free_option_is_not_tried_twice(key_file):
    fake = FakeOpenRouter([])
    chat(make_pipe(fake, key_file, FREE_MODELS="f1"), model="router.free")
    assert len(fake.requests) == 2


def test_error_mid_stream_keeps_the_text_and_adds_a_note(key_file):
    broken = (200, sse(delta("Partial"), {"error": {"message": "Upstream died", "code": 502}}, done=False))
    fake = FakeOpenRouter([broken])
    text = chat(make_pipe(fake, key_file))
    assert text.startswith("Partial\n\n_")
    assert len(fake.requests) == 1


def test_missing_or_placeholder_key(tmp_path):
    fake = FakeOpenRouter([OK])
    assert chat(make_pipe(fake, tmp_path / "nope")) == router.FRIENDLY["key_missing"]
    placeholder = tmp_path / "key"
    placeholder.write_text("<paste the chat-server key here>\n")
    assert chat(make_pipe(fake, placeholder)) == router.FRIENDLY["key_missing"]
    assert fake.requests == []


def test_web_search_toggle_adds_the_plugin(key_file):
    fake = FakeOpenRouter([OK])
    emitter = Emitter()
    chat(make_pipe(fake, key_file), emitter=emitter, **{router.WEB_FLAG: True})
    assert fake.requests[0]["plugins"] == [{"id": "web", "engine": "parallel", "max_results": 3}]
    assert router.WEB_FLAG not in fake.requests[0]
    assert emitter.statuses()[0].endswith("web search")


def test_images_switch_to_vision_models(key_file):
    models = [
        {"id": "seeing", "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "blind", "architecture": {"input_modalities": ["text"]}},
    ]
    fake = FakeOpenRouter([OK], models=models)
    pipe = make_pipe(fake, key_file, AUTO_MODELS="blind, seeing")
    image = [{"type": "text", "text": "what is this?"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    chat(pipe, content=image)
    assert fake.requests[0]["models"] == ["seeing"]


def test_chain_without_vision_uses_the_vision_list(key_file):
    models = [{"id": "v1", "architecture": {"input_modalities": ["image"]}}]
    fake = FakeOpenRouter([OK], models=models)
    pipe = make_pipe(fake, key_file, AUTO_MODELS="blind", VISION_MODELS="v1, unknown")
    chat(pipe, content=[{"type": "image_url", "image_url": {"url": "data:x"}}])
    assert fake.requests[0]["models"] == ["v1"]


def test_background_tasks_use_the_task_model(key_file):
    fake = FakeOpenRouter([(200, sse(delta(reasoning="hmm"), delta('{"title": "Hi"}'), usage()))])
    text = chat(make_pipe(fake, key_file, TASK_MODEL="cheap/model"), task="title_generation")
    assert text == '{"title": "Hi"}'
    assert fake.requests[0]["models"] == ["cheap/model"]


def test_create_image_tries_the_next_model_on_failure(key_file):
    png = base64.b64encode(b"fake-image").decode()
    images = [
        (502, {"error": {"message": "generation failed"}}),
        (200, {"data": [{"b64_json": png, "media_type": "image/webp"}], "usage": {"cost": 0.018}}),
    ]
    fake = FakeOpenRouter(images=images)
    emitter = Emitter()
    pipe = make_pipe(fake, key_file, IMAGE_MODELS="img/a, img/b")
    text = chat(pipe, content="a red lighthouse", emitter=emitter, **{router.IMAGE_FLAG: True})
    assert text == f"![a red lighthouse](data:image/webp;base64,{png})"  # stored inline outside Open WebUI
    assert [r["model"] for r in fake.image_requests] == ["img/a", "img/b"]
    assert emitter.statuses() == ["img/b · 0 tokens · $0.0180"]
    assert fake.requests == []


def test_create_image_alt_text_stays_on_one_line(key_file):
    png = base64.b64encode(b"fake-image").decode()
    fake = FakeOpenRouter(images=[(200, {"data": [{"b64_json": png, "media_type": "image/webp"}]})])
    prompt = "generate a bird [image]\n\nmake the bird beautiful and colourful"
    text = chat(make_pipe(fake, key_file), content=prompt, **{router.IMAGE_FLAG: True})
    alt = "generate a bird image make the bird beautiful and colourful"
    assert text == f"![{alt}](data:image/webp;base64,{png})"
    assert fake.image_requests[0]["prompt"] == prompt  # the model still gets the prompt as written


def test_create_image_needs_a_description(key_file):
    fake = FakeOpenRouter()
    assert "Describe the image" in chat(make_pipe(fake, key_file), content="  ", **{router.IMAGE_FLAG: True})


def test_a_chats_own_max_tokens_is_passed_on(key_file):
    fake = FakeOpenRouter([OK])
    chat(make_pipe(fake, key_file), max_tokens=5000)
    assert fake.requests[0]["max_tokens"] == 5000


# ---------- the classifier (CR-4 §3.1) ----------

PNG = base64.b64encode(b"fake-image").decode()
IMAGE_OK = (200, {"data": [{"b64_json": PNG, "media_type": "image/webp"}], "usage": {"cost": 0.007}})
PLAIN_LABELS = {"image": False, "web": False, "code": False, "voice": "none"}


def turn(pipe, content, message_id, messages=None, user="user-1", chat_id="chat-1", **kwargs):
    metadata = {"chat_id": chat_id, "message_id": message_id}
    return chat(pipe, content=content, messages=messages, user=user, metadata=metadata, **kwargs)


def misses(pipe) -> list[tuple]:
    with sqlite3.connect(pipe.valves.STORE_PATH) as con:
        return con.execute("SELECT user_id, text, labels, signal, strong FROM detections ORDER BY id").fetchall()


def test_classifier_call_asks_for_the_schema_privately(key_file):
    fake = FakeOpenRouter([OK])
    chat(make_pipe(fake, key_file, CLASSIFIER_MODEL="cheap/classifier"), content="what's new?")
    payload = fake.classify_requests[0]
    assert payload["models"] == ["cheap/classifier"]
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["reasoning"] == {"enabled": False}
    assert payload["provider"] == {"allow_fallbacks": True, "require_parameters": True, "data_collection": "deny"}
    assert payload["user"] == "user-1"
    system, user = (m["content"] for m in payload["messages"])
    assert '"draw up a contract for a rental flat" → chat' in system
    assert user == "Voice mode in this chat: off\nMessage: what's new?"


def test_classifier_retries_with_logging_allowed(key_file):
    no_private = (404, {"error": {"message": "No endpoints found matching your data policy"}})
    fake = FakeOpenRouter([OK], intents=[no_private, intent(web=True)])
    chat(make_pipe(fake, key_file))
    assert [r["provider"]["data_collection"] for r in fake.classify_requests] == ["deny", "allow"]
    assert "plugins" in fake.requests[0]


def test_web_turn_adds_the_plugin_and_names_it_in_the_footer(key_file):
    fake = FakeOpenRouter([OK], intents=[intent(web=True, cost=0.0001)])
    emitter = Emitter()
    chat(make_pipe(fake, key_file), content="weather in Chennai today?", emitter=emitter)
    assert fake.requests[0]["plugins"] == [{"id": "web", "engine": "parallel", "max_results": 3}]
    # the classifier's cost counts towards the reply
    assert emitter.statuses() == ["vendor/model · 42 tokens · $0.0013 · auto: web search"]


def test_image_turn_uses_the_classifiers_prompt(key_file):
    prompt = "A red fox in snow under a full moon"
    fake = FakeOpenRouter(images=[IMAGE_OK], intents=[intent(image=True, image_prompt=prompt)])
    emitter = Emitter()
    text = chat(make_pipe(fake, key_file, IMAGE_MODELS="img/a"), content="same, but at night", emitter=emitter)
    assert fake.image_requests[0]["prompt"] == prompt
    assert text.startswith(f"![{prompt}](")
    assert emitter.statuses() == ["img/a · 0 tokens · $0.0070 · auto: image"]
    assert fake.requests == []


def test_image_toggle_skips_the_classifier(key_file):
    fake = FakeOpenRouter(images=[IMAGE_OK])
    chat(make_pipe(fake, key_file), content="a lighthouse", **{router.IMAGE_FLAG: True})
    assert fake.classify_requests == []


def test_failed_classifier_means_plain_chat(key_file):
    broken = (500, {"error": {"message": "upstream down"}})
    fake = FakeOpenRouter([OK], intents=[broken, broken])
    assert chat(make_pipe(fake, key_file), content="draw me a cat") == "Hello there"
    assert "plugins" not in fake.requests[0]


def test_unreadable_classifier_answer_means_plain_chat(key_file):
    fake = FakeOpenRouter([OK], intents=[(200, sse(delta("sure, an image"), usage()))])
    assert chat(make_pipe(fake, key_file)) == "Hello there"


def test_slow_classifier_means_plain_chat(key_file):
    slow = (200, Slow(delta('{"image": true}'), usage(), pause=0.3))
    fake = FakeOpenRouter([OK], intents=[slow])
    assert chat(make_pipe(fake, key_file, CLASSIFIER_TIMEOUT_SECONDS=0.1), content="draw a cat") == "Hello there"


def test_a_turn_reuses_one_connection(key_file, monkeypatch):
    # A new client per call cost about 0.7 s each (CR-4 O5)
    made, real = [], router.httpx.AsyncClient
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **kwargs: made.append(1) or real(**kwargs))
    fake = FakeOpenRouter([OK], intents=[intent(web=True)])
    chat(make_pipe(fake, key_file))
    assert len(fake.classify_requests) == 1 and len(fake.requests) == 1
    assert len(made) == 1


def test_titles_never_wait_for_the_classifier(key_file):
    fake = FakeOpenRouter([(200, sse(delta('{"title": "Hi"}'), usage()))])
    chat(make_pipe(fake, key_file), task="title_generation")
    assert fake.classify_requests == []


def test_previous_turn_and_a_long_message_reach_the_classifier(key_file):
    fake = FakeOpenRouter([OK])
    history = [
        {"role": "user", "content": "Draw a red fox in the snow"},
        {"role": "assistant", "content": "![a red fox](/api/v1/files/1/content)"},
    ]
    long = "start " + "x" * 5000 + " read it out loud"
    chat(make_pipe(fake, key_file), content=long, messages=history)
    user = fake.classify_requests[0]["messages"][1]["content"]
    assert user.startswith("Previous user message: Draw a red fox in the snow\nPrevious reply was: an image\n")
    assert "Message: start x" in user and user.endswith(" read it out loud") and len(user) < 2200


def test_reading_the_classifier_with_care():
    # The voice mode the chat is already in changes nothing (CR-4 D16, O5)
    assert router.read_intent({"voice": "off"}, voice_on=False, has_previous=True)["voice"] == "none"
    assert router.read_intent({"voice": "on"}, voice_on=True, has_previous=True)["voice"] == "none"
    assert router.read_intent({"voice": "off"}, voice_on=True, has_previous=True)["voice"] == "off"
    assert router.read_intent({"voice": "loud"}, voice_on=False, has_previous=False)["voice"] == "none"
    # A correction needs a previous turn
    fix = {"image": True, "corrects_previous": {**PLAIN_LABELS, "image": True}}
    assert router.read_intent(fix, voice_on=False, has_previous=False)["corrects"] is None
    assert router.read_intent(fix, voice_on=False, has_previous=True)["corrects"]["image"] is True
    # An image prompt only for an image turn
    assert router.read_intent({"image_prompt": "a cat"}, voice_on=False, has_previous=False)["image_prompt"] == ""


# ---------- voice mode and the miss log (CR-4 §3.5, §3.7) ----------

def test_voice_mode_turns_on_and_off_for_the_chat(key_file):
    fake = FakeOpenRouter([OK, OK, OK], intents=[intent(voice="on"), intent(), intent(voice="off")])
    pipe = make_pipe(fake, key_file)
    turn(pipe, "from now on, voice notes please", "r1")
    turn(pipe, "tell me about owls", "r2")
    turn(pipe, "stop the voice notes", "r3")
    modes = [r["messages"][1]["content"].split("\n")[0] for r in fake.classify_requests]
    assert modes == ["Voice mode in this chat: off", "Voice mode in this chat: on", "Voice mode in this chat: on"]
    assert pipe._remember("voice_on", "chat-1") is False


def voiced(pipe) -> list[str]:
    with sqlite3.connect(pipe.valves.STORE_PATH) as con:
        return [row[0] for row in con.execute("SELECT message_id FROM voice_messages ORDER BY at")]


def test_replies_marked_for_voice_notes(key_file):
    # once: this reply only; on: this and the next; off: none after (CR-4 §3.5)
    intents = [intent(voice="once"), intent(), intent(voice="on"), intent(), intent(voice="off"), intent()]
    fake = FakeOpenRouter([OK] * 6, intents=intents)
    pipe = make_pipe(fake, key_file)
    for n, text in enumerate(["say it aloud", "and text?", "voice from now on", "owls?", "stop voice", "ok"], 1):
        turn(pipe, text, f"r{n}")
    assert voiced(pipe) == ["r1", "r3", "r4"]


def test_image_turns_are_never_voiced(key_file):
    fake = FakeOpenRouter(images=[IMAGE_OK], intents=[intent(image=True, image_prompt="a cat", voice="once")])
    pipe = make_pipe(fake, key_file)
    turn(pipe, "draw a cat and say it aloud", "r1")
    pipe._remember("set_voice", "chat-1", False)  # make sure the store exists for the check below
    assert voiced(pipe) == []


def test_the_router_and_the_voice_filter_share_the_mark(key_file):
    voice = load("functions/voice_note.py")
    fake = FakeOpenRouter([OK], intents=[intent(voice="once")])
    pipe = make_pipe(fake, key_file)
    turn(pipe, "read it to me", "r1")
    f = voice.Filter()
    f.valves = f.Valves(STORE_PATH=pipe.valves.STORE_PATH)
    assert f.marked("r1") and not f.marked("r2")


def test_correction_becomes_a_learned_example_for_that_user_only(key_file):
    fix = {**PLAIN_LABELS, "image": True}
    intents = [intent(), intent(image=True, image_prompt="a sunset", corrects_previous=fix)]
    fake = FakeOpenRouter([OK, OK, OK], images=[IMAGE_OK], intents=intents)
    pipe = make_pipe(fake, key_file)
    first = [{"role": "user", "content": "Show me a sunset over Marina beach"}, {"role": "assistant", "content": "It glows."}]
    turn(pipe, "Show me a sunset over Marina beach", "r1")
    turn(pipe, "No, I meant a picture", "r2", messages=first)
    assert misses(pipe) == [("user-1", "Show me a sunset over Marina beach", json.dumps(fix), "correction", 1)]
    turn(pipe, "hello", "r3", chat_id="chat-2")
    turn(pipe, "hello", "r4", user="user-2", chat_id="chat-3")
    learned = '"Show me a sunset over Marina beach" → image'
    assert learned in fake.classify_requests[2]["messages"][0]["content"]
    assert learned not in fake.classify_requests[3]["messages"][0]["content"]


def test_correction_that_matches_what_happened_is_not_a_miss(key_file):
    fix = {**PLAIN_LABELS, "image": True}
    intents = [intent(image=True, image_prompt="a fox"), intent(image=True, image_prompt="a fox", corrects_previous=fix)]
    fake = FakeOpenRouter(images=[IMAGE_OK, IMAGE_OK], intents=intents)
    pipe = make_pipe(fake, key_file)
    turn(pipe, "draw a fox", "r1")
    history = [{"role": "user", "content": "draw a fox"}, {"role": "assistant", "content": "![a fox](x)"}]
    turn(pipe, "no, draw it", "r2", messages=history)
    assert misses(pipe) == []


def test_learned_examples_are_capped_newest_first_and_deduplicated(tmp_path):
    store = router.Store(str(tmp_path / "s" / "router.sqlite"))
    for text in ("one", "two", "one", "three"):
        store.add_miss("u", "c", "m", text, PLAIN_LABELS, "tool", True)
    store.add_miss("u", "c", "m", "weak", PLAIN_LABELS, "rating", False)
    assert [t for t, _ in store.examples("u", 10)] == ["three", "one", "two"]
    assert [t for t, _ in store.examples("u", 2)] == ["three", "one"]
    assert store.examples("someone else", 10) == []
    store.add_miss("u", "c", "m", "x" * 1000, PLAIN_LABELS, "tool", True)
    assert len(store.examples("u", 1)[0][0]) == router.TEXT_LIMIT


def test_old_rows_go_when_their_table_is_written(tmp_path):
    store = router.Store(str(tmp_path / "router.sqlite"))
    store.add_miss("u", "c", "m", "old miss", PLAIN_LABELS, "tool", True)
    store.set_last_turn("old-chat", "u", "m", "old turn", PLAIN_LABELS)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE detections SET at = at - ?", (router.MISS_SECONDS + 1,))
        con.execute("UPDATE turns SET at = at - ?", (router.TURN_SECONDS + 1,))
    store.add_miss("u", "c", "m", "new miss", PLAIN_LABELS, "tool", True)
    store.set_last_turn("new-chat", "u", "m", "new turn", PLAIN_LABELS)
    assert [t for t, _ in store.examples("u", 10)] == ["new miss"]
    assert store.last_turn("old-chat") is None


def test_thumbs_down_is_logged_once_and_never_learned(key_file, monkeypatch):
    rating = types.SimpleNamespace(meta={"message_id": "r1"}, data={"rating": -1})

    class Feedbacks:
        @staticmethod
        async def get_feedbacks_by_user_id(user_id, limit=30):
            return types.SimpleNamespace(items=[rating])

    module = types.ModuleType("open_webui.models.feedbacks")
    module.Feedbacks = Feedbacks
    monkeypatch.setitem(sys.modules, "open_webui.models.feedbacks", module)
    fake = FakeOpenRouter([OK, OK, OK])
    pipe = make_pipe(fake, key_file)
    turn(pipe, "tell me about tea", "r1")
    turn(pipe, "and coffee?", "r2")
    turn(pipe, "and cocoa?", "r3")
    assert [(text, signal, strong) for _, text, _, signal, strong in misses(pipe)] == [("tell me about tea", "rating", 0)]
    assert pipe._remember("examples", "user-1", 20) == []


def test_a_broken_store_never_stops_a_reply(key_file, tmp_path):
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file where the store's folder should be")
    fake = FakeOpenRouter([OK], intents=[intent(voice="on")])
    pipe = make_pipe(fake, key_file, STORE_PATH=str(blocked / "router.sqlite"))
    assert turn(pipe, "voice notes from now on", "r1") == "Hello there"


# ---------- tools (CR-4 §3.3, §3.4) ----------

TOOLY = [{"id": "vendor/model", "supported_parameters": ["tools"]}]


def tool_pipe(fake, key_file, **valves):
    return make_pipe(fake, key_file, AUTO_MODELS="vendor/model", FREE_MODELS="f1", **valves)


def offered(payload) -> list[str]:
    return [t.get("function", {}).get("name") or t["type"] for t in payload.get("tools", [])]


class Browser:
    """Open WebUI's __event_call__ for execute:python: answers with `result`, or raises it."""

    def __init__(self, result):
        self.result, self.events = result, []

    async def __call__(self, event):
        self.events.append(event)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_tools_only_when_every_model_in_the_batch_takes_them(key_file):
    models = [{"id": "a", "supported_parameters": ["tools"]}, {"id": "b", "supported_parameters": []}]
    fake = FakeOpenRouter([OK, OK], models=models)
    chat(make_pipe(fake, key_file, AUTO_MODELS="a, b"))
    chat(make_pipe(fake, key_file, AUTO_MODELS="a"), ask=Browser({}))
    assert "tools" not in fake.requests[0]  # a mixed batch would quietly drop b (CR-4 D6, O4)
    assert offered(fake.requests[1]) == ["generate_image", "openrouter:web_search", "run_python", "voice_reply"]
    web = fake.requests[1]["tools"][1]["parameters"]
    assert web == {"engine": "parallel", "max_results": 3, "max_uses": 2, "mode": "fast"}


def test_tools_the_classifier_chose_are_not_offered_again(key_file):
    fake = FakeOpenRouter([OK], models=TOOLY, intents=[intent(web=True, voice="once")])
    chat(tool_pipe(fake, key_file))
    assert offered(fake.requests[0]) == ["generate_image"]  # no browser: no run_python either
    assert "plugins" in fake.requests[0]


def test_generate_image_tool_shows_the_image_and_the_model_goes_on(key_file):
    first = (200, sse(delta(reasoning="They want a picture."), tool_call("generate_image", {"prompt": "a lighthouse"}),
                      usage(cost=0.001)))
    fake = FakeOpenRouter([first, (200, sse(delta("Here it is."), usage()))], models=TOOLY, images=[IMAGE_OK])
    emitter = Emitter()
    pipe = tool_pipe(fake, key_file)
    text = turn(pipe, "can you sketch me a lighthouse?", "r1", emitter=emitter)
    assert f"\n\n![a lighthouse](data:image/webp;base64,{PNG})\n\n" in text and text.endswith("Here it is.")
    follow = fake.requests[1]
    assert follow["models"] == ["vendor/model"]  # the model that answered, not the whole batch
    asked, result = follow["messages"][-2:]
    assert asked["role"] == "assistant" and asked["content"] == ""
    assert asked["tool_calls"][0]["function"] == {"name": "generate_image", "arguments": '{"prompt": "a lighthouse"}'}
    assert result["role"] == "tool" and result["tool_call_id"] == "call-1"
    assert emitter.statuses() == ["Making the image…", "vendor/model · 84 tokens · $0.0092 · auto: image"]
    labels = {**PLAIN_LABELS, "image": True}  # the classifier said chat: a miss it learns from
    assert misses(pipe) == [("user-1", "can you sketch me a lighthouse?", json.dumps(labels), "tool", 1)]


def test_run_python_tool_runs_in_the_browser_and_shows_code_output_and_chart(key_file):
    code = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()\nprint(6 * 7)"
    first = (200, sse(tool_call("run_python", {"code": code}), usage()))
    fake = FakeOpenRouter([first, (200, sse(delta("It's 42."), usage()))], models=TOOLY,
                          intents=[intent(code=True)])
    browser = Browser({"stdout": "data:image/png;base64,iVBORw0KGgo=\n42", "stderr": "", "result": "None"})
    text = turn(tool_pipe(fake, key_file), "plot it and multiply", "r1", ask=browser)
    event = browser.events[0]
    assert event["type"] == "execute:python" and event["data"]["code"] == code
    assert "<details>\n<summary>Python</summary>" in text and "```python\nimport matplotlib" in text
    assert "```\n[chart shown to the user]\n42\n```" in text
    assert "![chart](data:image/png;base64,iVBORw0KGgo=)" in text and text.endswith("It's 42.")
    assert fake.requests[1]["messages"][-1]["content"] == "stdout:\n[chart shown to the user]\n42"


def test_code_turn_uses_tool_models_and_says_to_run_code(key_file):
    models = [{"id": "t1", "supported_parameters": ["tools"]}, {"id": "plain"}]
    fake = FakeOpenRouter([OK], models=models, intents=[intent(code=True)])
    pipe = make_pipe(fake, key_file, AUTO_MODELS="plain", TOOL_MODELS="t1")
    chat(pipe, content="chart my savings", ask=Browser({}))
    payload = fake.requests[0]
    assert payload["models"] == ["t1"] and "run_python" in offered(payload)
    assert payload["messages"][0] == {"role": "system", "content": router.CODE_NOTE}


def test_browser_that_cant_run_code_is_told_to_the_model(key_file):
    first = (200, sse(tool_call("run_python", {"code": "print(1)"}), usage()))
    fake = FakeOpenRouter([first, OK], models=TOOLY)
    text = chat(tool_pipe(fake, key_file), ask=Browser(TimeoutError("tab closed")))
    assert "<details>" not in text and text.endswith("Hello there")
    assert "couldn't run in the user's browser" in fake.requests[1]["messages"][-1]["content"]


def test_a_tool_is_capped_per_reply(key_file):
    again = lambda n: (200, sse(tool_call("generate_image", {"prompt": "a cat"}, call_id=f"c{n}"), usage()))
    fake = FakeOpenRouter([again(1), again(2), OK], models=TOOLY, images=[IMAGE_OK])
    chat(tool_pipe(fake, key_file))
    assert len(fake.image_requests) == 1
    assert "can't be used again" in fake.requests[2]["messages"][-1]["content"]


def test_last_tool_round_must_answer_in_words(key_file):
    loop = lambda n: (200, sse(tool_call("voice_reply", {"mode": "once"}, call_id=f"c{n}"), usage()))
    fake = FakeOpenRouter([loop(1), loop(2), loop(3), OK], models=TOOLY)
    chat(tool_pipe(fake, key_file, MAX_TOOL_ROUNDS=3))
    assert len(fake.requests) == 4  # three rounds of tool calls, then the answer
    assert [r.get("tool_choice") for r in fake.requests] == [None, None, None, "none"]


def test_voice_reply_tool_turns_voice_mode_on(key_file):
    first = (200, sse(tool_call("voice_reply", {"mode": "on"}), usage()))
    fake = FakeOpenRouter([first, OK], models=TOOLY)
    pipe = tool_pipe(fake, key_file)
    turn(pipe, "talk to me from now on", "r1")
    assert pipe._remember("voice_on", "chat-1") is True
    assert voiced(pipe) == ["r1"]
    assert "every reply in this chat" in fake.requests[1]["messages"][-1]["content"]


def test_openrouters_web_search_counts_as_web(key_file):
    fake = FakeOpenRouter([(200, sse(delta("It rained."), usage(searches=2)))], models=TOOLY)
    emitter = Emitter()
    pipe = tool_pipe(fake, key_file)
    turn(pipe, "did it rain in Chennai?", "r1", emitter=emitter)
    assert emitter.statuses()[-1].endswith("auto: web search")
    assert misses(pipe)[0][2] == json.dumps({**PLAIN_LABELS, "web": True})


def test_failure_after_a_tool_ran_doesnt_start_over(key_file):
    first = (200, sse(tool_call("generate_image", {"prompt": "a cat"}), usage()))
    fake = FakeOpenRouter([first, (500, {"error": {"message": "down"}})], models=TOOLY, images=[IMAGE_OK])
    text = chat(tool_pipe(fake, key_file))
    assert text.count("![a cat]") == 1 and text.endswith(f"_{router.FRIENDLY['other']}_")
    assert len(fake.requests) == 2 and len(fake.image_requests) == 1


def test_unknown_tool_is_answered_not_run(key_file):
    first = (200, sse(tool_call("delete_everything", {}), usage()))
    fake = FakeOpenRouter([first, OK], models=TOOLY)
    chat(tool_pipe(fake, key_file))
    assert fake.requests[1]["messages"][-1]["content"] == "There is no tool called delete_everything."


def test_labelled_set_is_well_formed_and_apart_from_the_seed_examples():
    seeds = router.SEED_EXAMPLES.lower()
    for line in (ROOT / "tests" / "classifier_intents.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        assert set(item["want"]) <= {"image", "web", "code", "voice"}
        assert item["msg"].lower() not in seeds
