"""Tests for the competitor teardown diff in teardown.py.

compare() takes two Phase 2 analyses and produces the ranked "why they win"
diff: the score gap, per-factor leads, the gaps sorted by the competitor's
lead, your wins, and the specific problem pulled from your page for each factor.
"""

import teardown
from rubric import FACTOR_LABELS

KEYS = list(FACTOR_LABELS)


def analysis(scores, issues=None, maxes=None):
    """Build a Phase 2-shaped analysis from {factor_key: score}."""
    issues = issues or {}
    maxes = maxes or {}
    breakdown = {}
    for key in KEYS:
        breakdown[key] = {
            "score": scores.get(key, 0),
            "max": maxes.get(key, 20),
            "issues": issues.get(key, []),
        }
    return {
        "source": "x",
        "total_score": sum(scores.get(k, 0) for k in KEYS),
        "breakdown": breakdown,
    }


def test_score_gap_is_positive_when_competitor_leads():
    you = analysis({"header_quality": 0})
    them = analysis({"header_quality": 20})
    result = teardown.compare(you, them)
    assert result["your_score"] == 0
    assert result["their_score"] == 20
    assert result["score_gap"] == 20


def test_gaps_are_ranked_by_competitor_lead():
    you = analysis({"header_quality": 5, "factual_specificity": 10, "recency_signals": 15})
    them = analysis({"header_quality": 20, "factual_specificity": 12, "recency_signals": 0})
    result = teardown.compare(you, them)
    # header lead 15, factual lead 2; recency is a loss so it's not a gap
    assert [g["key"] for g in result["gaps"]] == ["header_quality", "factual_specificity"]
    assert result["gaps"][0]["lead"] == 15


def test_your_wins_lists_factors_you_lead():
    you = analysis({"recency_signals": 15})
    them = analysis({"recency_signals": 0})
    result = teardown.compare(you, them)
    assert [w["key"] for w in result["your_wins"]] == ["recency_signals"]
    assert result["gaps"] == []


def test_leader_and_tie_flags():
    you = analysis({"header_quality": 10, "recency_signals": 15})
    them = analysis({"header_quality": 20, "recency_signals": 15})
    factors = {f["key"]: f for f in teardown.compare(you, them)["factors"]}
    assert factors["header_quality"]["leader"] == "them"
    assert factors["recency_signals"]["leader"] == "tie"


def test_every_factor_carries_a_recommendation():
    result = teardown.compare(analysis({}), analysis({}))
    assert len(result["factors"]) == len(KEYS)
    assert all(f["recommendation"] for f in result["factors"])


def test_your_issue_surfaces_a_problem_from_your_page():
    you = analysis(
        {"header_quality": 0},
        issues={"header_quality": ["No H1 heading was found on the page."]},
    )
    them = analysis({"header_quality": 20})
    gap = teardown.compare(you, them)["gaps"][0]
    assert gap["your_issue"] == "No H1 heading was found on the page."


def test_your_issue_is_none_when_no_problem_listed():
    you = analysis({"header_quality": 0}, issues={"header_quality": ["Clean heading hierarchy detected."]})
    them = analysis({"header_quality": 20})
    gap = teardown.compare(you, them)["gaps"][0]
    assert gap["your_issue"] is None
