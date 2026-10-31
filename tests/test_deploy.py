"""The deploy files: the compose stack, the settings file and the Open WebUI version guard (CR-2)."""

import re

import yaml

from conftest import ROOT, load

check = load("deploy/check_version.py")

COMPOSE_TEXT = (ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)
SERVICES = COMPOSE["services"]


def settings() -> dict[str, str]:
    pairs = {}
    for line in (ROOT / "deploy/settings.env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, value = line.split("=", 1)
            pairs[key] = value
    return pairs


def test_settings_are_plain_assignments():
    # bash sources the file and these tests parse it: no quotes, spaces or expansions, so both read the same values.
    for line in (ROOT / "deploy/settings.env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\s\"'$`\\]*", line), line


def test_settings_never_set_what_the_stack_sets():
    # The entrypoint loads settings.env over the environment, so an overlap would silently override the stack (D6).
    assert not set(settings()) & set(SERVICES["webui"]["environment"])


def test_settings_keep_the_cr1_choices():
    s = settings()
    assert s["ENABLE_OPENAI_API"] == s["ENABLE_OLLAMA_API"] == "false"
    assert (s["ENABLE_SIGNUP"], s["DEFAULT_USER_ROLE"]) == ("true", "pending")
    assert s["BYPASS_MODEL_ACCESS_CONTROL"] == "true"


def test_open_webui_is_the_official_image_at_one_exact_version():
    images = {SERVICES[name]["image"] for name in ("init", "webui")}
    assert len(images) == 1, f"init and webui must run the same Open WebUI: {images}"
    assert re.fullmatch(r"ghcr\.io/open-webui/open-webui:v\d+\.\d+\.\d+", images.pop())


def test_no_image_of_our_own():
    assert not any("selvalogesh" in service["image"] for service in SERVICES.values())


def test_webui_starts_from_the_fetched_copy():
    webui = SERVICES["webui"]
    assert webui["command"] == ["bash", "/app/chat/deploy/entrypoint.sh"]
    assert "${CHAT}/app:/app/chat:ro" in webui["volumes"]
    assert "${CHAT}/secrets:/run/chat-secrets:ro" in webui["volumes"]


def test_init_script_escapes_every_dollar():
    # Compose reads "$x" as a stack variable and silently empties it; the script's own "$" must be written "$$".
    script = SERVICES["init"]["command"][-1]
    assert "$$CHAT_REF" in script
    assert "$" not in script.replace("$$", "")


def test_init_fetches_only_the_folders_it_needs():
    script = SERVICES["init"]["command"][-1]
    assert "git archive FETCH_HEAD functions deploy" in script
    assert all((ROOT / folder).is_dir() for folder in ("functions", "deploy"))


def test_every_stack_variable_is_in_the_example_environment():
    example = (ROOT / "deploy/.env.example").read_text(encoding="utf-8")
    for name in set(re.findall(r"\$\{(\w+)", COMPOSE_TEXT)) - {"CHAT"}:  # CHAT comes from OMV's global.env
        assert re.search(rf"^{name}=", example, re.M), name


def test_the_guard_reads_the_repo_compose():
    assert check.pinned_versions(COMPOSE_TEXT) == {SERVICES["webui"]["image"].split(":v")[1]}


def test_the_guard_passes_a_matching_version():
    assert check.problem("0.12.5", {"0.12.5"}, "abc123") is None


def test_the_guard_stops_a_different_version():
    message = check.problem("0.12.6", {"0.12.5"}, "abc123")
    assert "runs Open WebUI 0.12.6" in message and "abc123 expects 0.12.5" in message
    assert "paste deploy/compose.yaml" in message


def test_the_guard_stops_unequal_or_missing_pins():
    assert "found ['0.12.5', '0.12.6']" in check.problem("0.12.5", {"0.12.5", "0.12.6"}, "abc123")
    assert "found none" in check.problem("0.12.5", set(), "abc123")


def test_deployed_gives_the_commit_and_ref():
    assert check.deployed("abc123 stable 2026-10-31T12:00:00Z\n") == ("abc123", "stable")
    assert check.deployed("") == ("unknown", "unknown")
