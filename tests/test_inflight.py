"""Tests for the in-flight guard on the expensive, uncancellable endpoints.

The competitor and rewrite checks each run for minutes and bill an API per
question. The dev server is threaded and keeps a request running even after the
browser abandons it, so a double-click (or a reload-and-retry) would otherwise
fan out into several concurrent runs for the same target. app._single_flight
admits one run per key and the endpoints return 409 for a duplicate.
"""

import os
import tempfile

import pytest

import app as app_module
import db


def test_single_flight_rejects_concurrent_same_key():
    key = "competitors:example.com"
    with app_module._single_flight(key):
        with pytest.raises(app_module._AlreadyRunning):
            with app_module._single_flight(key):
                pass
    # released on exit, so the key is free again
    assert key not in app_module._inflight


def test_single_flight_releases_on_error():
    key = "rewrite:https://example.com/a"
    with pytest.raises(ValueError):
        with app_module._single_flight(key):
            raise ValueError("boom")
    assert key not in app_module._inflight


def test_different_keys_run_concurrently():
    with app_module._single_flight("competitors:a.com"):
        with app_module._single_flight("competitors:b.com"):
            assert {"competitors:a.com", "competitors:b.com"} <= app_module._inflight


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", __import__("pathlib").Path(path))
    db.init_db()
    yield app_module.app.test_client()
    os.unlink(path)


def test_competitors_endpoint_returns_409_when_already_running(client, monkeypatch):
    # Pretend a run for this domain is already in flight; the endpoint must
    # refuse a second one rather than start another billable run.
    monkeypatch.setattr(app_module.harness, "provider_availability", lambda name: (True, ""))
    app_module._inflight.add("competitors:example.com")
    try:
        resp = client.post("/api/competitors", json={
            "target": "example.com",
            "queries": ["best widgets"],
            "engines": ["claude"],
        })
        assert resp.status_code == 409
        assert "already running" in resp.get_json()["error"]
    finally:
        app_module._inflight.discard("competitors:example.com")


def test_rewrite_endpoint_returns_409_when_already_running(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    url = "https://example.com/post"
    app_module._inflight.add(f"rewrite:{url}")
    try:
        resp = client.post("/api/rewrite", json={"url": url})
        assert resp.status_code == 409
        assert "already running" in resp.get_json()["error"]
    finally:
        app_module._inflight.discard(f"rewrite:{url}")
