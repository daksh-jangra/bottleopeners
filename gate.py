"""CI/API gate: fail a build when a page's citation-readiness score is too low.

Drops CitePilot into a dev workflow. Run it in CI against a deployed URL (or a
local HTML file before deploy) to block a merge when a page regresses below a
floor:

    python gate.py https://example.com/guide --min 70
    python gate.py dist/guide.html --file --min 80 --json

Exit codes are chosen for CI: 0 when the score meets the threshold, 1 when it
falls below (fail the build), 2 on a fetch/read error - so the step fails loudly
instead of silently shipping a page answer engines won't quote. Scoring reuses
the same analyzer the dashboard uses, and writes nothing to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

import analyzer
import ingest
import rubric

# Default floor: a "C" - good enough to be quotable, with room to improve.
DEFAULT_MIN = 70


def evaluate(score: int, minimum: int) -> dict[str, Any]:
    """Decide pass/fail for a score against a threshold. Pure - no I/O.

    The boundary passes: a score exactly equal to the minimum is acceptable.
    `deficit` is how many points short a failing score is (0 when passing).
    """
    passed = score >= minimum
    return {
        "score": score,
        "minimum": minimum,
        "passed": passed,
        "deficit": 0 if passed else minimum - score,
    }


def score_page(source: str, is_file: bool = False) -> dict[str, Any]:
    """Fetch (or read) and score a page. Returns {source, score, grade}.

    Uses ingest's SSRF-guarded fetcher for URLs and its file reader for local
    HTML. No run is saved and no brand index is snapshotted - the gate is a
    read-only check.
    """
    html = ingest.load_file(source) if is_file else ingest.fetch_url(source)
    payload = ingest.extract_html_payload(html, source)
    total = int(analyzer.build_analysis(payload)["total_score"])
    return {"source": source, "score": total, "grade": rubric.grade_for(total)}


def run_gate(source: str, minimum: int, is_file: bool = False) -> dict[str, Any]:
    """Score a page and apply the threshold, returning the merged verdict."""
    scored = score_page(source, is_file=is_file)
    return {**scored, **evaluate(scored["score"], minimum)}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gate",
        description="Fail CI when a page's citation-readiness score is below a threshold.",
    )
    parser.add_argument("source", help="A page URL, or a local HTML file with --file.")
    parser.add_argument("--min", type=int, default=DEFAULT_MIN,
                        help=f"Minimum passing score, 0-100 (default {DEFAULT_MIN}).")
    parser.add_argument("--file", action="store_true",
                        help="Treat source as a local HTML file instead of a URL.")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    args = parser.parse_args(argv)

    try:
        result = run_gate(args.source, args.min, is_file=args.file)
    except ingest.IngestError as exc:
        print(f"gate: could not read {args.source}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        stream = sys.stdout if result["passed"] else sys.stderr
        print(f"{status}  {result['score']}/100 ({result['grade']})  "
              f"min {result['minimum']}  {result['source']}", file=stream)
        if not result["passed"]:
            print(f"  {result['deficit']} pts below the {result['minimum']} threshold", file=sys.stderr)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
