"""deploy/detections.py: the owner's view of the classifier's misses (CR-4 §3.7)."""

import sqlite3

import pytest

from conftest import load

detections = load("deploy/detections.py")
router = load("functions/router.py")

WEB = {"image": False, "web": True, "code": False, "voice": "none"}


@pytest.fixture
def paths(tmp_path):
    store = router.Store(str(tmp_path / "router.sqlite"))
    store.add_miss("u1", "c", "m1", "is it raining in Chennai?", WEB, "tool", True)
    store.add_miss("u2", "c", "m2", "tell me about tea", WEB, "rating", False)
    webui_db = tmp_path / "webui.db"
    with sqlite3.connect(webui_db) as con:
        con.execute('CREATE TABLE "user" (id TEXT, email TEXT)')
        con.execute('INSERT INTO "user" VALUES (?, ?), (?, ?)', ("u1", "amma@example.com", "u2", "appa@example.com"))
    return ["--store", store.path, "--webui-db", str(webui_db)]


def test_list_shows_people_by_email_and_what_is_learned(paths, capsys):
    assert detections.main(["list", *paths]) == 0
    out = capsys.readouterr().out
    assert "amma@example.com  tool (learned)  → web" in out and "is it raining in Chennai?" in out
    assert "appa@example.com  rating (listed only)" in out


def test_list_one_person(paths, capsys):
    detections.main(["list", "--user", "appa@example.com", *paths])
    out = capsys.readouterr().out
    assert "tell me about tea" in out and "Chennai" not in out


def test_delete_one_and_clear_one_person(paths, capsys):
    assert detections.main(["delete", "1", *paths]) == 0
    assert detections.main(["delete", "1", *paths]) == 1
    assert detections.main(["clear", "--user", "appa@example.com", *paths]) == 0
    detections.main(["list", *paths])
    assert capsys.readouterr().out.endswith("No misses.\n")


def test_clear_needs_a_person_or_all(paths):
    with pytest.raises(SystemExit):
        detections.main(["clear", *paths])


def test_no_store_yet(tmp_path, capsys):
    assert detections.main(["list", "--store", str(tmp_path / "none.sqlite")]) == 0
    assert "hasn't written its file" in capsys.readouterr().out
    assert not (tmp_path / "none.sqlite").exists()
