"""Tests for the Brand Visibility Index in brandindex.py.

The index is a weighted average of three 0-100 signals (quotability, citation
rate, positive sentiment), grouped by normalized domain, with weights
renormalized over whichever components exist. These lock down the math,
the domain grouping, latest-run selection, and the first-to-latest delta.
db access is stubbed so the tests need no database.
"""

import brandindex


def _stub_runs(monkeypatch, by_kind):
    """Stub db.runs_by_kind to return canned rows (newest first) per kind."""
    def fake(kind, limit=1000):
        return by_kind.get(kind, [])
    monkeypatch.setattr(brandindex.db, "runs_by_kind", fake)


def run(target, score, created_at="2026-01-01T00:00:00+00:00"):
    return {"target": target, "score": score, "created_at": created_at}


# --- compute_index: the weighted average -----------------------------------

def test_index_all_three_components(monkeypatch):
    _stub_runs(monkeypatch, {
        "analyze": [run("https://amazon.in/deals", 80)],
        "competitors": [run("amazon.in", 40)],
        "sentiment": [run("amazon.in", 100)],
    })
    result = brandindex.compute_index("amazon.in")
    # 0.4*80 + 0.4*40 + 0.2*100 = 68
    assert result["index"] == 68
    assert all(c["present"] for c in result["components"])


def test_index_renormalizes_when_a_component_is_missing(monkeypatch):
    _stub_runs(monkeypatch, {
        "analyze": [run("https://amazon.in/x", 80)],
        "competitors": [run("amazon.in", 40)],
        # no sentiment run
    })
    result = brandindex.compute_index("amazon.in")
    # weights renormalize to 0.5/0.5 -> (80+40)/2 = 60, not dragged down by the gap
    assert result["index"] == 60
    sentiment = next(c for c in result["components"] if c["kind"] == "sentiment")
    assert sentiment["present"] is False and sentiment["value"] is None


def test_index_none_when_no_runs(monkeypatch):
    _stub_runs(monkeypatch, {})
    result = brandindex.compute_index("amazon.in")
    assert result["index"] is None
    assert all(not c["present"] for c in result["components"])


# --- domain grouping & latest selection ------------------------------------

def test_groups_analyze_url_by_domain_and_ignores_other_domains(monkeypatch):
    _stub_runs(monkeypatch, {
        "analyze": [
            run("https://flipkart.com/x", 90),   # different domain - ignored
            run("https://amazon.in/y", 70),
        ],
    })
    result = brandindex.compute_index("amazon.in")
    assert result["index"] == 70


def test_picks_the_latest_run_per_component(monkeypatch):
    # runs_by_kind yields newest first; the newest matching wins
    _stub_runs(monkeypatch, {
        "analyze": [
            run("https://amazon.in/new", 82, "2026-03-01T00:00:00+00:00"),
            run("https://amazon.in/old", 50, "2026-01-01T00:00:00+00:00"),
        ],
    })
    result = brandindex.compute_index("amazon.in")
    assert result["index"] == 82


def test_normalizes_www_and_scheme(monkeypatch):
    _stub_runs(monkeypatch, {"competitors": [run("https://www.amazon.in/", 55)]})
    result = brandindex.compute_index("amazon.in")
    assert result["index"] == 55


# --- list_brands ------------------------------------------------------------

def test_list_brands_dedupes_across_kinds(monkeypatch):
    _stub_runs(monkeypatch, {
        "analyze": [run("https://amazon.in/a", 80), run("https://flipkart.com/b", 60)],
        "competitors": [run("amazon.in", 40)],
        "sentiment": [run("www.flipkart.com", 70)],
    })
    assert brandindex.list_brands() == ["amazon.in", "flipkart.com"]


# --- brand_index: trend + delta --------------------------------------------

def test_brand_index_reports_delta_from_first_snapshot(monkeypatch):
    _stub_runs(monkeypatch, {
        "analyze": [run("https://amazon.in/x", 80)],
        "competitors": [run("amazon.in", 40)],
        "sentiment": [run("amazon.in", 100)],
    })
    monkeypatch.setattr(brandindex.db, "history", lambda kind, target: [
        {"score": 50, "created_at": "2026-01-01T00:00:00+00:00"},
        {"score": 68, "created_at": "2026-02-01T00:00:00+00:00"},
    ])
    result = brandindex.brand_index("amazon.in", record=False)
    assert result["index"] == 68
    assert result["delta"] == 18          # 68 now vs 50 first snapshot
    assert result["snapshots"] == 2
    assert len(result["history"]) == 2
