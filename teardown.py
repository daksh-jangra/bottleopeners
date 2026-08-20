"""Competitor teardown: diff a competitor's citation-readiness against yours.

Turns "they won the citation and you didn't" into "here is exactly where they
beat you, and what to change." Both pages run through the same Phase 2 analysis;
this module diffs the six factors and ranks the gaps by the competitor's lead,
attaching the fix recommendation and your page's specific problem for each.
"""

from __future__ import annotations

from typing import Any

from audit import first_issue
from rubric import FACTOR_LABELS, RECOMMENDATIONS


def _factor_row(key: str, label: str, you_section: dict, them_section: dict) -> dict[str, Any]:
    you_score = int(you_section.get("score", 0))
    them_score = int(them_section.get("score", 0))
    max_score = int(you_section.get("max", them_section.get("max", 0)))
    lead = them_score - you_score  # positive => the competitor is ahead
    return {
        "key": key,
        "label": label,
        "you": you_score,
        "them": them_score,
        "max": max_score,
        "lead": lead,
        "leader": "them" if lead > 0 else ("you" if lead < 0 else "tie"),
        "recommendation": RECOMMENDATIONS.get(key, ""),
        # the single most relevant problem on YOUR page for this factor (or None)
        "your_issue": first_issue(you_section),
    }


def compare(you: dict[str, Any], them: dict[str, Any]) -> dict[str, Any]:
    """Diff two Phase 2 analyses into a ranked, actionable teardown."""
    your_breakdown = you.get("breakdown", {})
    their_breakdown = them.get("breakdown", {})

    factors = [
        _factor_row(key, label, your_breakdown.get(key, {}), their_breakdown.get(key, {}))
        for key, label in FACTOR_LABELS.items()
    ]

    # Where the competitor beats you, biggest lead first - that is the playbook.
    gaps = sorted(
        (f for f in factors if f["lead"] > 0),
        key=lambda f: (-f["lead"], f["label"]),
    )
    your_wins = [f for f in factors if f["lead"] < 0]

    your_total = int(you.get("total_score", 0))
    their_total = int(them.get("total_score", 0))
    return {
        "your_score": your_total,
        "their_score": their_total,
        "score_gap": their_total - your_total,
        "factors": factors,
        "gaps": gaps,
        "your_wins": your_wins,
    }
