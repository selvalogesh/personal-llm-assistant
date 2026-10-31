from pathlib import Path

from conftest import ROOT, load

loader = load("deploy/load_functions.py")


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


def test_dockerignore_ships_the_functions_and_loader():
    allowed = Path(ROOT / ".dockerignore").read_text().splitlines()
    assert "!functions/" in allowed and "!deploy/load_functions.py" in allowed
