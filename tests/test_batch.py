"""Integration tests for the site-wide batch endpoint (/api/batch) in app.py.

The network and the scoring pipeline are stubbed so no page is really fetched:
ingest.fetch_url returns canned robots/sitemap/HTML, and the analyze pieces are
replaced with cheap fakes. These prove the endpoint's own logic - sitemap
discovery, the page cap, that every page is stored as a tracked run, and that
the brand index is snapshotted once per domain rather than once per page.
"""

import os
import tempfile

import pytest

import app as app_module
import db


ROBOTS = "User-agent: *\nSitemap: https://example.com/sitemap.xml\n"
SITEMAP = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://example.com/c</loc></url>
</urlset>"""


@pytest.fixture
def client(monkeypatch):
    # Point the DB at a throwaway file so runs don't touch the real citepilot.db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", __import__("pathlib").Path(path))
    db.init_db()

    # Stub the network: robots + sitemap by URL, empty HTML for pages
    def fake_fetch(url):
        if url.endswith("/robots.txt"):
            return ROBOTS
        if url.endswith("sitemap.xml"):
            return SITEMAP
        return "<html><body>page</body></html>"
    monkeypatch.setattr(app_module.ingest, "fetch_url", fake_fetch)

    # Stub the scoring pipeline so each page scores deterministically by path
    monkeypatch.setattr(app_module, "_fetch_payload", lambda url: {"title": url, "url": url})
    monkeypatch.setattr(app_module.analyzer, "build_analysis", lambda p: {"url": p["url"]})
    monkeypatch.setattr(app_module, "_schema_for", lambda p: {})
    monkeypatch.setattr(app_module.rubric, "build_report",
                        lambda a, s, x: {"score": 70, "grade": "B", "priorities": [], "strengths": []})

    # Count brand-index snapshots to prove it's once per domain
    calls = {"snapshots": 0}
    monkeypatch.setattr(app_module, "_snapshot_bvi", lambda t: calls.__setitem__("snapshots", calls["snapshots"] + 1))
    app_module.app.config["SNAP_CALLS"] = calls

    yield app_module.app.test_client()
    os.unlink(path)


def test_batch_discovers_analyzes_and_tracks_all_pages(client):
    resp = client.post("/api/batch", json={"target": "example.com"})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["found"] == 3
    assert data["analyzed"] == 3
    assert data["capped"] is False
    # every discovered page is now a tracked analyze target
    tracked = {t["target"] for t in db.tracked_targets("analyze")}
    assert tracked == {"https://example.com/a", "https://example.com/b", "https://example.com/c"}


def test_batch_snapshots_brand_index_once_per_domain(client):
    client.post("/api/batch", json={"target": "example.com"})
    # three pages, one domain -> exactly one snapshot
    assert app_module.app.config["SNAP_CALLS"]["snapshots"] == 1


def test_batch_caps_pages(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_BATCH_URLS", 2)
    resp = client.post("/api/batch", json={"target": "example.com"})
    data = resp.get_json()
    assert data["found"] == 3
    assert data["analyzed"] == 2       # only the first 2 were analyzed
    assert data["capped"] is True
    assert data["cap"] == 2


def test_batch_requires_a_target(client):
    resp = client.post("/api/batch", json={"target": "   "})
    assert resp.status_code == 400


def test_batch_404_when_no_sitemap(client, monkeypatch):
    monkeypatch.setattr(app_module.ingest, "fetch_url",
                        lambda url: (_ for _ in ()).throw(app_module.ingest.IngestError("nope")))
    resp = client.post("/api/batch", json={"target": "example.com"})
    assert resp.status_code == 404
