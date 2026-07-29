"""Phase 4 LLM rewriter for AI-citation optimization.

Rewrites Phase 1 content so it is easier for AI answer engines to quote:
answer-first openings, fact-dense sentences, explicit list/table structure,
and clean headers. The rewrite is emitted in the same shape as Phase 1 output,
so Phase 2 (analyzer) and Phase 3 (generator) can run on it unchanged and prove
the score lift.

Usage:
  python rewriter.py --input ./output/<slug>.json
  python rewriter.py --input ./output/<slug>.json --analysis ./output/analyzed/<slug>.json
  python rewriter.py --input ./output/<slug>.json --dry-run

This phase is the first that calls a model. Set ANTHROPIC_API_KEY in the
environment before a live run:

  export ANTHROPIC_API_KEY=sk-ant-...
  python rewriter.py --input ./output/<slug>.json

Until the key is set, --dry-run builds and prints the exact request that would
be sent, so the whole pipeline can be verified without spending anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from common import DEFAULT_MODEL, slugify

DEFAULT_MAX_TOKENS = 16000

REQUIRED_FIELDS = {
    "source": str,
    "title": str,
    "meta_description": (str, type(None)),
    "headers": list,
    "body_text": str,
    "existing_schema": (str, type(None)),
    "published_date": (str, type(None)),
    "updated_date": (str, type(None)),
    "word_count": int,
}

# The model returns this shape; structured outputs guarantee it validates.
REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "meta_description": {"type": "string"},
        "answer_summary": {"type": "string"},
        "headers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["level", "text"],
                "additionalProperties": False,
            },
        },
        "body_text": {"type": "string"},
        "faq": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question", "answer"],
                "additionalProperties": False,
            },
        },
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "list_count": {"type": "integer"},
        "table_count": {"type": "integer"},
        "changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "meta_description",
        "answer_summary",
        "headers",
        "body_text",
        "faq",
        "key_facts",
        "list_count",
        "table_count",
        "changes",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are an editor who rewrites web content so AI answer engines (Claude, "
    "ChatGPT, Perplexity, Google AI Overviews) are more likely to quote and cite "
    "it. You optimize for citation-readiness, not keyword stuffing. Your rewrites "
    "must stay factually faithful to the source: never invent statistics, dates, "
    "names, or claims that are not supported by the original text. Apply these "
    "principles:\n"
    "1. Answer-first: open the very first sentence of the body with a short "
    "definition (under 18 words) using a form of 'is', 'are', 'means', "
    "'requires', or 'includes' - e.g. 'Descaling is the removal of mineral "
    "buildup from a coffee maker.' Then open each following section with a "
    "direct, quotable answer in its first one or two sentences.\n"
    "2. Fact density: prefer concrete numbers, dates, and named entities over "
    "vague phrasing, but only facts present in the source.\n"
    "3. Explicit structure: convert step sequences into numbered lists and "
    "comparisons into tables so discrete points are easy to extract.\n"
    "4. Specific headers: make H2/H3 headers descriptive and question-shaped "
    "where natural, not generic labels like 'Steps' or 'Overview'.\n"
    "5. Self-contained sentences: each key sentence should make sense quoted on "
    "its own, without surrounding context."
)


class RewriterError(Exception):
    pass


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env into the environment.

    Values already set in the real environment win, so an explicit export is
    never overridden. Kept dependency-free on purpose.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def load_json(path: str, label: str) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.exists():
        raise RewriterError(f"{label} file not found: {path}")
    if not input_path.is_file():
        raise RewriterError(f"{label} path is not a file: {path}")
    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = input_path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RewriterError(f"{label} file is not valid JSON: {exc}") from exc


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RewriterError("Input JSON must be an object.")
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise RewriterError(f"Input JSON is missing required fields: {', '.join(missing)}")
    for field, expected_type in REQUIRED_FIELDS.items():
        value = payload[field]
        if not isinstance(value, expected_type):
            if field in {"meta_description", "existing_schema", "published_date", "updated_date"} and value is None:
                continue
            type_name = expected_type if isinstance(expected_type, tuple) else expected_type.__name__
            raise RewriterError(f"Field '{field}' has the wrong type; expected {type_name}.")
    return payload


def summarize_weaknesses(analysis: dict[str, Any]) -> list[str]:
    """Pull the concrete issues from a Phase 2 analysis so the rewrite targets them."""
    breakdown = analysis.get("breakdown")
    if not isinstance(breakdown, dict):
        return []
    weaknesses: list[str] = []
    for section_name, section in breakdown.items():
        if not isinstance(section, dict):
            continue
        score = section.get("score")
        max_score = section.get("max")
        issues = section.get("issues")
        header = section_name.replace("_", " ")
        if isinstance(score, int) and isinstance(max_score, int):
            weaknesses.append(f"{header}: scored {score}/{max_score}")
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, str):
                    weaknesses.append(f"  - {issue}")
    return weaknesses


def build_user_prompt(payload: dict[str, Any], weaknesses: list[str]) -> str:
    headers = payload.get("headers") or []
    header_lines = "\n".join(
        f"  H{h.get('level')}: {h.get('text')}"
        for h in headers
        if isinstance(h, dict)
    ) or "  (none)"

    parts = [
        "Rewrite the following content for AI citation-readiness. Keep it "
        "factually faithful to the source.",
        "",
        f"TITLE: {payload.get('title')}",
        f"META DESCRIPTION: {payload.get('meta_description') or '(none)'}",
        "HEADERS:",
        header_lines,
        "",
        "BODY:",
        str(payload.get("body_text", "")),
    ]

    if weaknesses:
        parts += [
            "",
            "This content was scored by the Phase 2 analyzer. Prioritize fixing "
            "these specific weaknesses:",
            *weaknesses,
        ]

    parts += [
        "",
        "Return the rewrite via the structured output schema. In `list_count` "
        "and `table_count`, report how many distinct lists and tables your "
        "rewritten body actually contains, and reflect that structure in "
        "`body_text`. In `changes`, briefly note what you improved and why.",
    ]
    return "\n".join(parts)


def build_request(payload: dict[str, Any], weaknesses: list[str], model: str) -> dict[str, Any]:
    """Assemble the exact Messages API request. No key or network needed."""
    return {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM_PROMPT,
        "output_config": {"format": {"type": "json_schema", "schema": REWRITE_SCHEMA}},
        "messages": [{"role": "user", "content": build_user_prompt(payload, weaknesses)}],
    }


def call_claude(request: dict[str, Any]) -> dict[str, Any]:
    """Send the request to Claude and return the parsed rewrite object."""
    try:
        import anthropic
    except ImportError as exc:
        raise RewriterError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic()
    try:
        message = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise RewriterError("Authentication failed; check ANTHROPIC_API_KEY.") from exc
    except anthropic.APIError as exc:
        raise RewriterError(f"Claude API request failed: {exc}") from exc

    text = next((block.text for block in message.content if block.type == "text"), None)
    if not text:
        raise RewriterError("Claude returned no text content to parse.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RewriterError(f"Claude returned invalid JSON: {exc}") from exc


def assemble_rewritten_payload(source_payload: dict[str, Any], rewrite: dict[str, Any]) -> dict[str, Any]:
    """Merge the model rewrite into a Phase 1-compatible payload for re-analysis."""
    body_text = str(rewrite.get("body_text", "")).strip()
    headers = [
        {"level": int(h["level"]), "text": str(h["text"])}
        for h in rewrite.get("headers", [])
        if isinstance(h, dict) and "level" in h and "text" in h
    ]
    return {
        "source": source_payload["source"],
        "title": str(rewrite.get("title") or source_payload["title"]),
        "meta_description": rewrite.get("meta_description") or source_payload.get("meta_description"),
        "headers": headers,
        "body_text": body_text,
        "existing_schema": source_payload.get("existing_schema"),
        "published_date": source_payload.get("published_date"),
        "updated_date": source_payload.get("updated_date"),
        "word_count": len(body_text.split()),
        "list_count": max(0, int(rewrite.get("list_count", 0) or 0)),
        "table_count": max(0, int(rewrite.get("table_count", 0) or 0)),
    }


def write_output(result: dict[str, Any], title: str) -> Path:
    output_dir = Path.cwd() / "output" / "rewritten"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slugify(title)}.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 4 LLM rewriter for AI-citation optimization.")
    parser.add_argument("--input", required=True, help="Path to a Phase 1 output JSON file.")
    parser.add_argument("--analysis", help="Optional Phase 2 analysis JSON, used to target weak areas.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL}).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print the request without calling the API. No key required.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        load_dotenv()
        args = parse_args()
        payload = validate_payload(load_json(args.input, "Input"))

        weaknesses: list[str] = []
        if args.analysis:
            weaknesses = summarize_weaknesses(load_json(args.analysis, "Analysis"))

        request = build_request(payload, weaknesses, args.model)

        if args.dry_run:
            preview = {
                "dry_run": True,
                "model": request["model"],
                "system": request["system"],
                "output_schema": "REWRITE_SCHEMA (structured output enforced)",
                "user_prompt": request["messages"][0]["content"],
            }
            json.dump(preview, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RewriterError(
                "ANTHROPIC_API_KEY is not set. Set it to run a live rewrite, or use "
                "--dry-run to preview the request without a key."
            )

        rewrite = call_claude(request)
        rewritten_payload = assemble_rewritten_payload(payload, rewrite)
        result = {
            "source": payload["source"],
            "model": request["model"],
            "rewritten_payload": rewritten_payload,
            "faq": rewrite.get("faq", []),
            "key_facts": rewrite.get("key_facts", []),
            "answer_summary": rewrite.get("answer_summary", ""),
            "changes": rewrite.get("changes", []),
        }

        write_output(result, str(rewritten_payload["title"]))
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except RewriterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while rewriting content.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
