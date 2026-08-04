"""Tests for Drift Watch score-movement math in drift.py.

Pure series math, no database. These pin the first-to-latest delta and
direction, the too-few-points guard, dropping of missing scores, and the
biggest-mover-first ranking.
"""

import drift


# --- drift_series -----------------------------------------------------------

def test_upward_drift():
    m = drift.drift_series([51, 55, 59])
    assert m["first"] == 51 and m["latest"] == 59
    assert m["delta"] == 8 and m["direction"] == "up"
    assert m["points"] == 3 and m["min"] == 51 and m["max"] == 59


def test_downward_drift():
    m = drift.drift_series([80, 72])
    assert m["delta"] == -8 and m["direction"] == "down"


def test_flat_when_first_equals_latest():
    # dips in between but ends where it started -> flat, though range shows the dip
    m = drift.drift_series([70, 55, 70])
    assert m["delta"] == 0 and m["direction"] == "flat"
    assert m["min"] == 55 and m["max"] == 70


def test_single_point_is_not_drift():
    assert drift.drift_series([42]) is None
    assert drift.drift_series([]) is None


def test_missing_scores_are_dropped():
    m = drift.drift_series([None, 40, None, 60])
    assert m["first"] == 40 and m["latest"] == 60 and m["points"] == 2


def test_all_missing_is_none():
    assert drift.drift_series([None, None]) is None


# --- rank_movers ------------------------------------------------------------

def test_rank_orders_by_absolute_delta():
    items = [
        {"kind": "analyze", "target": "a", "scores": [50, 52]},        # +2
        {"kind": "analyze", "target": "b", "scores": [90, 60]},        # -30
        {"kind": "competitors", "target": "c", "scores": [40, 55]},    # +15
    ]
    ranked = drift.rank_movers(items)
    assert [r["target"] for r in ranked] == ["b", "c", "a"]           # 30, 15, 2 by magnitude
    assert ranked[0]["direction"] == "down"


def test_rank_drops_targets_without_enough_history():
    items = [
        {"kind": "analyze", "target": "solo", "scores": [77]},          # dropped
        {"kind": "analyze", "target": "pair", "scores": [10, 20]},
    ]
    ranked = drift.rank_movers(items)
    assert [r["target"] for r in ranked] == ["pair"]


def test_rank_is_stable_for_equal_deltas():
    items = [
        {"kind": "sentiment", "target": "z", "scores": [10, 15]},       # +5
        {"kind": "analyze", "target": "a", "scores": [20, 25]},         # +5
    ]
    ranked = drift.rank_movers(items)
    # equal magnitude -> tie broken by kind then target
    assert [(r["kind"], r["target"]) for r in ranked] == [("analyze", "a"), ("sentiment", "z")]
