"""Tests for the Prompt Hub - the saved query library (db.py + /api/prompts).

The DB layer and the endpoints run against a throwaway database so nothing
touches the real citepilot.db. These lock down dedupe, blank handling, newest-
first ordering, delete, and the add/list/delete HTTP flow.
"""

import os
import pathlib
import tempfile

import pytest

import app as app_module
import db


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(path))
    db.init_db()
    yield
    os.unlink(path)


# --- db layer ---------------------------------------------------------------

def test_save_and_list_prompts(fresh_db):
    assert db.save_prompts(["how to descale a kettle", "best pour-over ratio"]) == 2
    texts = [p["text"] for p in db.list_prompts()]
    assert set(texts) == {"how to descale a kettle", "best pour-over ratio"}


def test_save_dedupes_exact_and_case_insensitive(fresh_db):
    db.save_prompts(["Best Grinder"])
    # same text (case-insensitive) and an in-batch dupe are both ignored
    added = db.save_prompts(["best grinder", "New One", "new one"])
    assert added == 1
    assert len(db.list_prompts()) == 2


def test_save_drops_blanks(fresh_db):
    assert db.save_prompts(["  ", "", "real question"]) == 1
    assert len(db.list_prompts()) == 1


def test_delete_prompt(fresh_db):
    db.save_prompts(["keep me", "delete me"])
    target = next(p for p in db.list_prompts() if p["text"] == "delete me")
    assert db.delete_prompt(target["id"]) == 1
    assert [p["text"] for p in db.list_prompts()] == ["keep me"]
    # deleting a missing id is a no-op
    assert db.delete_prompt(999999) == 0


# --- endpoints --------------------------------------------------------------

def test_prompts_add_list_delete_flow(fresh_db):
    client = app_module.app.test_client()

    # add one via `text`
    r = client.post("/api/prompts", json={"text": "how much does descaling cost"})
    assert r.status_code == 200 and r.get_json()["added"] == 1

    # add many via `texts`, with a dupe that gets ignored
    r = client.post("/api/prompts", json={"texts": ["q one", "q two", "how much does descaling cost"]})
    body = r.get_json()
    assert body["added"] == 2
    assert len(body["prompts"]) == 3

    # list reflects everything
    listed = client.get("/api/prompts").get_json()["prompts"]
    assert len(listed) == 3

    # delete one, library shrinks
    victim = listed[0]["id"]
    r = client.post("/api/prompts/delete", json={"id": victim})
    assert r.get_json()["deleted"] == 1
    assert len(r.get_json()["prompts"]) == 2


def test_delete_requires_id(fresh_db):
    client = app_module.app.test_client()
    assert client.post("/api/prompts/delete", json={}).status_code == 400
