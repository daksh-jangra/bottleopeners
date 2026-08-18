"""Drift Watch: how each tracked target's score moves over time.

Pure, dependency-free series math. Given a target's score history (oldest to
newest), it reports the first-to-latest delta - the "51 -> 59" movement - plus
range and direction, and ranks targets so the biggest movers surface first.

Drift only means something once there are repeated data points, so it lands
after the pieces that produce them: the scheduled/manual pulse that re-checks
targets over time, and the Prompt Hub query set that drives the citation and
sentiment runs those points come from. A single run is not drift.
"""

from __future__ import annotations

from typing import Any, Optional

# A target needs at least this many scored runs before drift is meaningful.
MIN_POINTS = 2


def drift_series(scores: list[Optional[int]]) -> Optional[dict[str, Any]]:
    """Summarize a score series into drift metrics, or None if too short.

    Missing scores (None) are dropped first. With fewer than MIN_POINTS real
    scores there is nothing to compare, so the target is not a mover.
    """
    pts = [int(s) for s in scores if s is not None]
    if len(pts) < MIN_POINTS:
        return None
    first, latest = pts[0], pts[-1]
    delta = latest - first
    return {
        "first": first,
        "latest": latest,
        "delta": delta,
        "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
        "points": len(pts),
        "min": min(pts),
        "max": max(pts),
        "scores": pts,
    }


def rank_movers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn per-target score series into ranked drift rows.

    Each input item is {"kind", "target", "scores"}. Targets without enough
    history are dropped; the rest are returned biggest-absolute-move first, with
    ties broken by kind then target for a stable order.
    """
    movers: list[dict[str, Any]] = []
    for item in items:
        metrics = drift_series(item.get("scores", []))
        if metrics is None:
            continue
        movers.append({"kind": item.get("kind"), "target": item.get("target"), **metrics})
    movers.sort(key=lambda r: (-abs(r["delta"]), r.get("kind") or "", r.get("target") or ""))
    return movers
