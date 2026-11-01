import base64

from conftest import Asker, Emitter, FakeOpenRouter, Slow, collect, delta, load, run, sse, usage

router = load("functions/router.py")


def make_pipe(fake: FakeOpenRouter, key_file, **valves):
    pipe = router.Pipe()
    pipe.valves = pipe.Valves(API_KEY_FILE=str(key_file), **valves)
    pipe.transport = fake.transport
    return pipe


def chat(pipe, model="router.auto", content="hi", emitter=None, task=None, ask=None, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": content}], **extra}
    return run(collect_pipe(pipe, body, emitter, task, ask))


async def collect_pipe(pipe, body, emitter, task, ask=None):
    result = await pipe.pipe(
        body, __user__={"id": "user-1"}, __event_emitter__=emitter, __event_call__=ask, __task__=task
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
