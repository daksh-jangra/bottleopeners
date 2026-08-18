"""Phase 2 structure analyzer for AI-citation optimization.

This module validates Phase 1 JSON input, applies rule-based scoring,
and writes the result to output/analyzed/<slugified-title>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from common import slugify
from payload import validate_payload as _validate_payload
from rules import (
    score_answer_first_structure,
    score_byline_authority,
    score_factual_specificity,
    score_header_quality,
    score_list_table_presence,
    score_recency_signals,
)

class AnalyzerError(Exception):
    pass


def _validate_date(value: str, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise AnalyzerError(f"Field '{field_name}' must be an ISO date (YYYY-MM-DD) or null.") from exc


def validate_payload(payload: Any) -> dict[str, Any]:
    # The shared contract first; the analyzer is the strictest consumer and
    # layers its own header/date/count checks on top.
    _validate_payload(payload, AnalyzerError)

    headers = payload["headers"]
    for index, header in enumerate(headers):
        if not isinstance(header, dict):
            raise AnalyzerError(f"Header at index {index} must be an object with 'level' and 'text'.")
        if "level" not in header or "text" not in header:
            raise AnalyzerError(f"Header at index {index} is missing 'level' or 'text'.")
        if not isinstance(header["level"], int) or header["level"] not in {1, 2, 3}:
            raise AnalyzerError(f"Header at index {index} has invalid level; expected 1, 2, or 3.")
        if not isinstance(header["text"], str) or not header["text"].strip():
            raise AnalyzerError(f"Header at index {index} has invalid text; expected a non-empty string.")

    if payload["published_date"] is not None:
        _validate_date(payload["published_date"], "published_date")
    if payload["updated_date"] is not None:
        _validate_date(payload["updated_date"], "updated_date")

    if payload["word_count"] < 0:
        raise AnalyzerError("Field 'word_count' must be zero or greater.")

    # Optional Phase 1 structure counts; absent in payloads ingested before they existed.
    for field in ("list_count", "table_count"):
        value = payload.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise AnalyzerError(f"Field '{field}' must be an integer or null.")
        if value < 0:
            raise AnalyzerError(f"Field '{field}' must be zero or greater.")

    return payload


def load_input(path: str) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.exists():
        raise AnalyzerError(f"Input file not found: {path}")
    if not input_path.is_file():
        raise AnalyzerError(f"Input path is not a file: {path}")

    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = input_path.read_text(encoding="utf-8", errors="replace")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnalyzerError(f"Input file is not valid JSON: {exc}") from exc

    return validate_payload(payload)


def build_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    breakdown = {
        "header_quality": score_header_quality(payload),
        "answer_first_structure": score_answer_first_structure(payload),
        "list_table_presence": score_list_table_presence(payload),
        "factual_specificity": score_factual_specificity(payload),
        "byline_authority": score_byline_authority(payload),
        "recency_signals": score_recency_signals(payload),
    }
    total_score = sum(int(section["score"]) for section in breakdown.values())
    return {
        "source": payload["source"],
        "total_score": total_score,
        "breakdown": breakdown,
    }


def write_output(result: dict[str, Any], title: str) -> Path:
    output_dir = Path.cwd() / "output" / "analyzed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slugify(title)}.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2 structure analyzer for AI-citation optimization.")
    parser.add_argument("--input", required=True, help="Path to a Phase 1 output JSON file.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = load_input(args.input)
        result = build_analysis(payload)
        write_output(result, str(payload["title"]))
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except AnalyzerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while analyzing content.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())