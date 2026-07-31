"""Regression detection for the monitoring pulse.

Pure, dependency-free scoring logic: given a target's previous score and its
freshly re-checked score, decide whether the drop is big enough to raise an
alert. Kept separate from db/app so it is trivially unit-testable and has no
side effects - the caller is responsible for persisting any alert.
"""

from __future__ import annotations

from typing import Any, Optional

# A score must fall by at least this many points to count as a regression worth
# alerting on. Smaller run-to-run wobble stays quiet so alerts mean something.
DROP_THRESHOLD = 5


def detect_regression(
    previous_score: Optional[int],
    current_score: Optional[int],
    threshold: int = DROP_THRESHOLD,
) -> Optional[dict[str, Any]]:
    """Return regression details when `current` falls `threshold`+ points below
    `previous`, else None.

    A missing previous or current score - a brand-new target with no baseline,
    or a re-check that failed to score - is never a regression: there is nothing
    to fall from. A rise or a drop smaller than the threshold is quiet too.
    """
    if previous_score is None or current_score is None:
        return None
    delta = previous_score - current_score
    if delta < threshold:
        return None
    return {
        "previous_score": previous_score,
        "current_score": current_score,
        "delta": delta,
    }
