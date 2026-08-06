"""Tests for Mention Rate: how often the brand shows up at all (vs cite).

harness.mention_rate is pure and checked directly from sentiment-mix counts.
The endpoint test stubs the model call and confirms the sentiment run also
records a separate `mention` run, so mention rate accrues its own history in
Drift Watch. A throwaway DB keeps the real one untouched.
"""

import os
import pathlib
import tempfile

import pytest

import harness


def _mix(pos=0, neu=0, neg=0, none=0):
    counts = {"positive": pos, "neutral": neu, "negative": neg, "not_mentioned": none}
    total = pos + neu + neg + none
    return {k: {"count": v, "pct": round(100 * v / total) if total else 0} for k, v in counts.items()}


# --- mention_rate (pure) ----------------------------------------------------

def test_all_mentioned_is_100():
    assert harness.mention_rate(_mix(pos=2, neu=1, neg=1)) == 100


def test_none_mentioned_is_0():
    assert harness.mention_rate(_mix(none=4)) == 0


def test_counts_any_sentiment_as_a_mention():
    # 3 of 4 mention the brand (positive/neutral/negative), 1 not -> 75
    assert harness.mention_rate(_mix(pos=1, neu=1, neg=1, none=1)) == 75


def test_distinct_from_positive_share():
    # only 1 of 4 is positive, but 3 of 4 mention the brand
    mix = _mix(pos=1, neu=1, neg=1, none=1)
    assert mix["positive"]["pct"] == 25
    assert harness.mention_rate(mix) == 75


def test_empty_mix_is_0():
    assert harness.mention_rate(_mix()) == 0


# --- /api/sentiment records a mention run ------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import db
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(path))
    db.init_db()
    yield db
    os.unlink(path)


def test_sentiment_run_also_saves_a_mention_run(monkeypatch, fresh_db):
    import app as app_module
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # pass the endpoint's guard
    canned = {
        "brand": "acme",
        "queries": 4,
        "mix": _mix(pos=1, neu=1, neg=1, none=1),
        "mention_rate": 75,
        "results": [],
    }
    monkeypatch.setattr(app_module.harness, "run_sentiment", lambda brand, queries, model: canned)
    monkeypatch.setattr(app_module, "_snapshot_bvi", lambda t: None)

    client = app_module.app.test_client()
    r = client.post("/api/sentiment", json={"brand": "acme", "queries": ["q1", "q2"]})
    assert r.status_code == 200
    assert r.get_json()["mention_rate"] == 75

    # a mention run was recorded with the mention rate as its score
    mention_hist = fresh_db.history("mention", "acme")
    assert len(mention_hist) == 1 and mention_hist[0]["score"] == 75
    # the sentiment run is still recorded too (positive share)
    assert fresh_db.history("sentiment", "acme")[0]["score"] == 25
