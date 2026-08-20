"""Tests for the multi-engine citation harness and the /api/competitors merge.

Two things here fail silently if they break, which is why they get tests.

First, Gemini never returns a source URL - every grounding chunk points at a
Google redirector - so `_grounding_host` has to recover the real host from
`domain`/`title` or by following the redirect. If it regresses, the endpoint
still returns a full, plausible-looking table in which every row is
"vertexaisearch.cloud.google.com".

Second, the endpoint now merges two engines into one count. The rule is that a
domain scores once per *query* no matter how many engines cited it, so
`cited_in` stays within 0..len(queries) and the citation rate written to the
runs table keeps the meaning it had before Gemini existed (the Brand Visibility
Index weights that score at 0.4).

No API is called: both runners are monkeypatched to canned results.
"""

import os
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

import app as app_module
import db
import harness


# --- Gemini grounding host resolution ----------------------------------------

def web(domain=None, title=None, uri=None):
    """A stand-in for the SDK's GroundingChunkWeb."""
    return SimpleNamespace(domain=domain, title=title, uri=uri)


REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"


def test_grounding_host_prefers_the_domain_field():
    assert harness._grounding_host(web(domain="Faber.co.in", title="Faber", uri=REDIRECT)) == "faber.co.in"


def test_grounding_host_falls_back_to_a_host_shaped_title():
    # The Gemini API does not populate `domain`, so this is the common path.
    assert harness._grounding_host(web(title="www.faber.co.in", uri=REDIRECT)) == "faber.co.in"


def test_grounding_host_resolves_the_redirect_when_the_title_is_a_bare_name(monkeypatch):
    # Titles are sometimes a name rather than a host ("aljazeera", "Al Jazeera"),
    # which would normalize into a garbage domain if taken at face value.
    monkeypatch.setattr(harness, "_resolve_redirect", lambda uri: "aljazeera.com")
    assert harness._grounding_host(web(title="Al Jazeera", uri=REDIRECT)) == "aljazeera.com"
    assert harness._grounding_host(web(title="aljazeera", uri=REDIRECT)) == "aljazeera.com"


def test_grounding_host_never_credits_the_redirector(monkeypatch):
    # A redirect that fails to leave Google resolves nothing. Returning the
    # redirect host would make it the top competitor on every single query.
    monkeypatch.setattr(harness, "_resolve_redirect", lambda uri: harness.GEMINI_REDIRECT_HOST)
    assert harness._grounding_host(web(title="Faber", uri=REDIRECT)) == ""
    # ...and the redirect host must not sneak in through domain/title either.
    assert harness._grounding_host(web(title=harness.GEMINI_REDIRECT_HOST, uri="")) == ""


def test_grounding_host_survives_an_empty_chunk():
    assert harness._grounding_host(web()) == ""


def test_resolve_redirect_returns_empty_on_a_network_error(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.RequestException("timed out")

    monkeypatch.setattr(requests, "head", boom)
    assert harness._resolve_redirect(REDIRECT) == ""


# --- /api/competitors multi-engine merge -------------------------------------

@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", pathlib.Path(path))
    db.init_db()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(app_module, "_snapshot_bvi", lambda target: None)

    yield app_module.app.test_client()
    os.unlink(path)


def stub_engines(monkeypatch, claude=None, gemini=None):
    """Pin each engine's runner to fixed source lists, keyed by query."""
    def runner(results):
        return lambda query, model: (results.get(query, []), "answer")
    monkeypatch.setitem(harness.PROVIDERS["claude"], "runner", runner(claude or {}))
    monkeypatch.setitem(harness.PROVIDERS["gemini"], "runner", runner(gemini or {}))


def test_a_domain_cited_by_both_engines_counts_once_per_query(client, monkeypatch):
    stub_engines(
        monkeypatch,
        claude={"q1": ["https://faber.co.in/chimneys"], "q2": ["https://elica.com/x"]},
        gemini={"q1": ["faber.co.in"], "q2": ["faber.co.in"]},
    )
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1", "q2"]})
    body = res.get_json()
    assert res.status_code == 200

    rows = {c["domain"]: c for c in body["competitors"]}
    # Cited by both engines on q1 and by Gemini alone on q2 - two queries, not three.
    assert rows["faber.co.in"]["cited_in"] == 2
    assert rows["faber.co.in"]["engines"] == ["claude", "gemini"]
    assert rows["elica.com"]["cited_in"] == 1
    assert rows["elica.com"]["engines"] == ["claude"]


def test_cited_in_never_exceeds_the_query_count(client, monkeypatch):
    # The bar chart divides by d.queries, so a count above it would render >100%.
    stub_engines(
        monkeypatch,
        claude={"q1": ["https://faber.co.in/a", "https://www.faber.co.in/b"]},
        gemini={"q1": ["faber.co.in"]},
    )
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1"]})
    body = res.get_json()
    assert body["competitors"][0]["cited_in"] == 1
    assert body["queries"] == 1


def test_target_counts_a_query_once_when_either_engine_cites_it(client, monkeypatch):
    stub_engines(
        monkeypatch,
        claude={"q1": ["https://mysite.com/a"], "q2": []},
        gemini={"q1": ["mysite.com"], "q2": ["mysite.com"]},
    )
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1", "q2"]})
    body = res.get_json()

    assert body["target_cited"] == 2          # both queries, not three engine-hits
    per_engine = {e["name"]: e["cited"] for e in body["engines"]}
    assert per_engine == {"claude": 1, "gemini": 2}

    # The stored score keeps its pre-Gemini meaning: % of queries cited at all.
    assert db.history("competitors", "mysite.com")[-1]["score"] == 100


def test_a_subdomain_of_the_target_is_still_the_target(client, monkeypatch):
    stub_engines(monkeypatch, claude={"q1": ["https://blog.mysite.com/post"]})
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1"], "engines": ["claude"]})
    body = res.get_json()
    assert body["target_cited"] == 1
    assert all(c["is_target"] for c in body["competitors"])


def test_an_engine_without_a_key_is_skipped_not_fatal(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    stub_engines(monkeypatch, claude={"q1": ["https://faber.co.in/x"]})
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1"]})
    body = res.get_json()

    assert res.status_code == 200
    assert [e["name"] for e in body["engines"]] == ["claude"]
    assert body["skipped"][0]["name"] == "gemini"
    assert "GEMINI_API_KEY" in body["skipped"][0]["reason"]


def test_an_engine_that_fails_mid_run_is_retired_with_its_reason(client, monkeypatch):
    def explode(query, model):
        raise harness.HarnessError("Gemini API request failed: quota exhausted")

    stub_engines(monkeypatch, claude={"q1": ["https://faber.co.in/x"], "q2": ["https://elica.com/y"]})
    monkeypatch.setitem(harness.PROVIDERS["gemini"], "runner", explode)

    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1", "q2"]})
    body = res.get_json()

    # Claude's results survive, and the failure is reported rather than swallowed.
    assert res.status_code == 200
    assert {c["domain"] for c in body["competitors"]} == {"faber.co.in", "elica.com"}
    assert body["skipped"][0]["name"] == "gemini"
    assert "quota exhausted" in body["skipped"][0]["reason"]


def test_all_engines_failing_is_an_error(client, monkeypatch):
    def explode(query, model):
        raise harness.HarnessError("nope")

    monkeypatch.setitem(harness.PROVIDERS["claude"], "runner", explode)
    monkeypatch.setitem(harness.PROVIDERS["gemini"], "runner", explode)
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1"]})

    assert res.status_code == 400
    assert "nope" in res.get_json()["error"]


def test_no_engine_selected_is_rejected(client, monkeypatch):
    res = client.post("/api/competitors", json={"target": "mysite.com", "queries": ["q1"], "engines": []})
    assert res.status_code == 400
    assert "engine" in res.get_json()["error"].lower()
