import io
import urllib.error

from conftest import ROOT, load

loader = load("deploy/load_functions.py")


def fake_api(monkeypatch, installed: set[str]) -> list[tuple]:
    """Open WebUI's function API with `installed` ids; returns the calls made."""
    calls = []

    def call(method, path, token="", body=None):
        calls.append((method, path))
        function_id = path.split("/id/")[1].split("/")[0]
        if function_id not in installed:
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, io.BytesIO(b""))
        return {"id": function_id}

    monkeypatch.setattr(loader, "call", call)
    return calls


def test_retired_toggles_are_removed_when_installed(monkeypatch):
    calls = fake_api(monkeypatch, {"web_search"})
    loader.retire("token", "web_search")
    loader.retire("token", "image_mode")
    assert ("DELETE", "/api/v1/functions/id/web_search/delete") in calls
    assert not [c for c in calls if c[0] == "DELETE" and "image_mode" in c[1]]


def test_only_retired_ids_are_removed():
    # Functions the owner adds in the UI are never touched (CR-4 D7), and no retired id is still in the repo
    assert set(loader.RETIRED) == {"web_search", "image_mode"}
    assert not {p.stem for p in (ROOT / "functions").glob("*.py")} & set(loader.RETIRED)


def test_every_function_has_a_title_and_description():
    for path in sorted((ROOT / "functions").glob("*.py")):
        meta = loader.frontmatter(path.read_text(encoding="utf-8"))
        assert meta.get("title"), path.name
        assert meta.get("description"), path.name
        assert path.stem.isidentifier(), "Open WebUI ids allow only letters, digits and underscores"


def test_frontmatter_without_a_docstring_is_empty():
    assert loader.frontmatter("import os\n") == {}


def test_functions_import_nothing_from_this_repo():
    # Open WebUI stores and runs each function on its own (CLAUDE.md).
    for path in (ROOT / "functions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from functions" not in text and "import functions" not in text, path.name
