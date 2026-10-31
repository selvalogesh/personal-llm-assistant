import base64

from conftest import Emitter, FakeOpenRouter, collect, delta, load, run, sse, usage

router = load("functions/router.py")


def make_pipe(fake: FakeOpenRouter, key_file, **valves):
    pipe = router.Pipe()
    pipe.valves = pipe.Valves(API_KEY_FILE=str(key_file), **valves)
    pipe.transport = fake.transport
    return pipe


def chat(pipe, model="router.auto", content="hi", emitter=None, task=None, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": content}], **extra}
    return run(collect_pipe(pipe, body, emitter, task))


async def collect_pipe(pipe, body, emitter, task):
    result = await pipe.pipe(body, __user__={"id": "user-1"}, __event_emitter__=emitter, __task__=task)
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


def test_first_request_denies_logging_and_carries_user_and_cap(key_file):
    fake = FakeOpenRouter([OK])
    chat(make_pipe(fake, key_file, MAX_TOKENS=1000), max_tokens=5000, temperature=0.3)
    payload = fake.requests[0]
    assert payload["provider"] == {"data_collection": "deny", "allow_fallbacks": True}
    assert payload["user"] == "user-1"
    assert payload["max_tokens"] == 1000
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


def test_create_image_needs_a_description(key_file):
    fake = FakeOpenRouter()
    assert "Describe the image" in chat(make_pipe(fake, key_file), content="  ", **{router.IMAGE_FLAG: True})
