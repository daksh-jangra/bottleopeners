"""Tests for Persona Fan-Out in harness.py + the /api/persona endpoint.

The prompt framing, persona selection, and roll-up are pure and checked
directly. The fan-out orchestrator is exercised with the model calls stubbed
(run_claude + classify_sentiment), so no network or API key is touched.
"""

import harness


# --- pure helpers -----------------------------------------------------------

def test_persona_query_frames_the_asker():
    q = harness.persona_query("best kettle?", "a first-time buyer on a tight budget")
    assert q == "I am a first-time buyer on a tight budget. best kettle?"


def test_select_personas_empty_means_all():
    assert harness.select_personas([]) == harness.PERSONAS


def test_select_personas_filters_and_orders():
    picked = harness.select_personas(["enterprise", "beginner", "bogus"])
    # canonical order preserved (beginner before enterprise), unknown dropped
    assert [p["key"] for p in picked] == ["beginner", "enterprise"]


def test_summarize_counts_cited_and_mentioned():
    rows = [
        {"cited": True, "mentioned": True},
        {"cited": False, "mentioned": True},
        {"cited": False, "mentioned": False},
    ]
    s = harness.summarize_personas(rows)
    assert s == {"personas": 3, "cited": 1, "mentioned": 2}


# --- orchestrator (stubbed model) -------------------------------------------

def test_run_persona_fanout_builds_rows(monkeypatch):
    # amazon.in is cited only when the answer's urls include it
    def fake_run_claude(query, model):
        if "budget" in query:
            return (["https://amazon.in/deal"], "Amazon is great for budget buyers.")
        return (["https://other.com"], "Try a premium brand instead.")
    def fake_classify(brand, answer, model):
        mentioned = "amazon" in answer.lower()
        return {"mentioned": mentioned,
                "sentiment": "positive" if mentioned else "not_mentioned",
                "evidence": answer[:20]}
    monkeypatch.setattr(harness, "run_claude", fake_run_claude)
    monkeypatch.setattr(harness, "classify_sentiment", fake_classify)

    personas = harness.select_personas(["beginner", "professional"])
    result = harness.run_persona_fanout("amazon.in", "best option?", personas, "model")

    rows = {r["key"]: r for r in result["rows"]}
    assert rows["beginner"]["cited"] is True and rows["beginner"]["mentioned"] is True
    assert rows["professional"]["cited"] is False and rows["professional"]["mentioned"] is False
    assert result["summary"] == {"personas": 2, "cited": 1, "mentioned": 1}


# --- endpoint guards --------------------------------------------------------

def test_api_persona_requires_brand_and_query(monkeypatch):
    import app as app_module
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = app_module.app.test_client()
    assert client.post("/api/persona", json={"query": "q"}).status_code == 400          # no brand
    assert client.post("/api/persona", json={"brand": "b"}).status_code == 400           # no query


def test_api_persona_returns_rows(monkeypatch):
    import app as app_module
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(app_module.harness, "run_persona_fanout",
                        lambda brand, query, personas, model: {
                            "brand": brand, "query": query,
                            "rows": [{"key": p["key"], "persona": p["label"], "cited": False,
                                      "mentioned": False, "sentiment": "not_mentioned", "evidence": ""}
                                     for p in personas],
                            "summary": {"personas": len(personas), "cited": 0, "mentioned": 0},
                        })
    client = app_module.app.test_client()
    r = client.post("/api/persona", json={"brand": "acme", "query": "q", "personas": ["beginner", "smb"]})
    assert r.status_code == 200
    assert len(r.get_json()["rows"]) == 2
