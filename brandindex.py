"""Brand Visibility Index: one composite 0-100 score per brand/domain.

Combines the three model-facing signals we already store - page quotability
(analyze), citation rate (competitors), and positive-sentiment share - into a
single number for a domain. All three are already on a 0-100 scale, so the
index is a weighted average over whichever components exist, with the weights
renormalized so a missing signal doesn't drag the score down.

Runs are grouped by normalized domain (an analyze URL and a competitors domain
for the same site collapse to one brand). Each recompute saves a timestamped
snapshot (kind "bvi"), so a trend line and a first-to-latest delta accrue over
time - the same first/latest primitive the History tab already uses.
"""

from __future__ import annotations

from typing import Any, Optional

import db
from harness import normalize_domain

# Each component: the run kind it comes from, a UI label, and its weight. Weights
# are renormalized across the components actually present for a domain.
COMPONENTS: list[tuple[str, str, float]] = [
    ("analyze", "Page quotability", 0.4),
    ("competitors", "Citation rate", 0.4),
    ("sentiment", "Positive sentiment", 0.2),
]

BVI_KIND = "bvi"


def _latest_by_domain(kind: str, domain: str) -> Optional[dict[str, Any]]:
    """The most recent run of `kind` whose target normalizes to `domain`."""
    best: Optional[dict[str, Any]] = None
    for run in db.runs_by_kind(kind):
        if run["score"] is None:
            continue
        if normalize_domain(run["target"]) != domain:
            continue
        # runs_by_kind returns newest first, so the first match is the latest.
        best = run
        break
    return best


def compute_index(domain: str) -> dict[str, Any]:
    """Compute the current index and component breakdown for a domain.

    Pure read - no snapshot is written. Returns index=None when the domain has
    no scorable runs yet.
    """
    domain = normalize_domain(domain)
    components: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for kind, label, weight in COMPONENTS:
        run = _latest_by_domain(kind, domain)
        present = run is not None
        value = int(run["score"]) if present else None
        components.append({
            "kind": kind,
            "label": label,
            "weight": weight,
            "present": present,
            "value": value,
            "as_of": run["created_at"] if present else None,
        })
        if present:
            weighted_sum += value * weight
            weight_total += weight

    index = round(weighted_sum / weight_total) if weight_total else None
    return {"domain": domain, "index": index, "components": components}


def brand_index(domain: str, record: bool = True) -> dict[str, Any]:
    """Full dashboard view: current index, components, trend, and delta.

    When `record` is true and the index is computable, a snapshot is persisted
    first so it appears in the returned trend and future deltas measure from it.
    """
    result = compute_index(domain)
    if record and result["index"] is not None:
        db.save_run(BVI_KIND, result["domain"], result["index"], {
            "components": result["components"],
        })

    hist = db.history(BVI_KIND, result["domain"])
    result["history"] = [{"score": h["score"], "created_at": h["created_at"]} for h in hist]
    first = hist[0]["score"] if hist else None
    result["delta"] = (
        result["index"] - first
        if result["index"] is not None and first is not None
        else None
    )
    result["snapshots"] = len(hist)
    return result


def list_brands() -> list[str]:
    """Distinct normalized domains that have at least one component run."""
    domains: set[str] = set()
    for kind, _label, _weight in COMPONENTS:
        for run in db.runs_by_kind(kind):
            domain = normalize_domain(run["target"])
            if domain:
                domains.add(domain)
    return sorted(domains)
