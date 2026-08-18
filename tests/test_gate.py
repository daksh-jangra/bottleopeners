"""Tests for the CI/API score gate in gate.py.

The threshold logic is pure and checked directly. Scoring is stubbed so no page
is fetched: these lock down the pass/fail/boundary decision, the CLI exit codes
(0 pass / 1 fail / 2 error) that CI relies on, and the /api/gate endpoint.
"""

import gate


# --- evaluate ---------------------------------------------------------------

def test_pass_above_threshold():
    v = gate.evaluate(85, 70)
    assert v["passed"] is True and v["deficit"] == 0


def test_boundary_is_a_pass():
    # exactly at the minimum passes
    assert gate.evaluate(70, 70)["passed"] is True


def test_fail_below_threshold_reports_deficit():
    v = gate.evaluate(61, 70)
    assert v["passed"] is False and v["deficit"] == 9


# --- run_gate ---------------------------------------------------------------

def test_run_gate_merges_score_and_verdict(monkeypatch):
    monkeypatch.setattr(gate, "score_page", lambda source, is_file=False: {
        "source": source, "score": 82, "grade": "B",
    })
    r = gate.run_gate("https://x.com", 70)
    assert r["score"] == 82 and r["grade"] == "B" and r["passed"] is True


# --- CLI exit codes ---------------------------------------------------------

def test_cli_exit_0_when_passing(monkeypatch, capsys):
    monkeypatch.setattr(gate, "score_page", lambda s, is_file=False: {"source": s, "score": 90, "grade": "A"})
    assert gate.main(["https://x.com", "--min", "70"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_exit_1_when_failing(monkeypatch, capsys):
    monkeypatch.setattr(gate, "score_page", lambda s, is_file=False: {"source": s, "score": 55, "grade": "F"})
    assert gate.main(["https://x.com", "--min", "70"]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_cli_exit_2_on_fetch_error(monkeypatch, capsys):
    def boom(s, is_file=False):
        raise gate.ingest.IngestError("boom")
    monkeypatch.setattr(gate, "score_page", boom)
    assert gate.main(["https://x.com"]) == 2
    assert "could not read" in capsys.readouterr().err


def test_cli_json_output(monkeypatch, capsys):
    monkeypatch.setattr(gate, "score_page", lambda s, is_file=False: {"source": s, "score": 75, "grade": "C"})
    gate.main(["https://x.com", "--min", "70", "--json"])
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is True and out["score"] == 75


# --- /api/gate --------------------------------------------------------------

def test_api_gate_returns_verdict(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.gate, "score_page", lambda s, is_file=False: {"source": s, "score": 64, "grade": "D"})
    client = app_module.app.test_client()
    r = client.post("/api/gate", json={"url": "https://x.com", "min": 70})
    body = r.get_json()
    assert r.status_code == 200
    assert body["passed"] is False and body["deficit"] == 6


def test_api_gate_requires_url():
    import app as app_module
    assert app_module.app.test_client().post("/api/gate", json={}).status_code == 400
