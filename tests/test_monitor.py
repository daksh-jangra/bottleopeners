"""Tests for regression detection in monitor.py.

detect_regression is pure: given a target's previous and current score it
decides whether the drop clears the alert threshold. These lock down the
threshold boundary, the direction (only drops, never rises), and the
missing-baseline cases where there's nothing to fall from.
"""

import monitor


def test_drop_at_threshold_is_a_regression():
    r = monitor.detect_regression(80, 75)  # exactly DROP_THRESHOLD (5)
    assert r == {"previous_score": 80, "current_score": 75, "delta": 5}


def test_drop_above_threshold_is_a_regression():
    r = monitor.detect_regression(90, 60)
    assert r is not None and r["delta"] == 30


def test_drop_below_threshold_is_quiet():
    # a 4-point wobble is under the threshold, so no alert
    assert monitor.detect_regression(80, 76) is None


def test_a_rise_is_never_a_regression():
    assert monitor.detect_regression(70, 95) is None


def test_no_change_is_quiet():
    assert monitor.detect_regression(70, 70) is None


def test_missing_previous_score_is_quiet():
    # brand-new target with no baseline to fall from
    assert monitor.detect_regression(None, 40) is None


def test_missing_current_score_is_quiet():
    # a re-check that failed to produce a score
    assert monitor.detect_regression(80, None) is None


def test_custom_threshold_overrides_default():
    # a 6-point drop clears the default but not a stricter threshold of 10
    assert monitor.detect_regression(80, 74, threshold=10) is None
    assert monitor.detect_regression(80, 74, threshold=5) is not None
