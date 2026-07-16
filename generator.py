"""Phase 3 schema markup generator for AI-citation optimization."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from templates import build_article_schema, build_faq_schema, build_howto_schema

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

QUESTION_PREFIXES = ("what", "why", "how", "when", "where", "who", "which", "can", "do", "does", "is", "are", "should", "will")


class GeneratorError(Exception):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "output"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GeneratorError("Input JSON must be an object.")

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise GeneratorError(f"Input JSON is missing required fields: {', '.join(missing)}")

    for field, expected_type in REQUIRED_FIELDS.items():
        value = payload[field]
        if not isinstance(value, expected_type):
            if field in {"meta_description", "existing_schema", "published_date", "updated_date"} and value is None:
                continue
            type_name = expected_type if isinstance(expected_type, tuple) else expected_type.__name__
            raise GeneratorError(f"Field '{field}' has the wrong type; expected {type_name}.")

    return payload


def load_input(path: str) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.exists():
        raise GeneratorError(f"Input file not found: {path}")
    if not input_path.is_file():
        raise GeneratorError(f"Input path is not a file: {path}")

    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = input_path.read_text(encoding="utf-8", errors="replace")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"Input file is not valid JSON: {exc}") from exc

    return validate_payload(payload)


def clean_text(value: str) -> str:
    return normalize_whitespace(value)


def summarize_article_body(body_text: str, title: str) -> str:
    cleaned = clean_text(body_text)
    if not cleaned:
        return f"Referenced article body for {title}."

    first_sentence_split = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    excerpt = first_sentence_split[0]
    if len(excerpt) < 80 and len(first_sentence_split) > 1:
        excerpt = f"{excerpt} {first_sentence_split[1][:80]}".strip()

    excerpt = excerpt[:240].rstrip()
    if len(cleaned) > len(excerpt):
        excerpt = f"{excerpt}..."
    return excerpt


def header_texts(payload: dict[str, Any]) -> list[str]:
    return [clean_text(header["text"]) for header in payload["headers"]]


def find_sections(payload: dict[str, Any]) -> list[dict[str, str]]:
    body_text = clean_text(payload["body_text"])
    headers = header_texts(payload)
    sections: list[dict[str, str]] = []
    cursor = 0

    for index, header_text in enumerate(headers):
        start = body_text.find(header_text, cursor)
        if start == -1:
            continue
        content_start = start + len(header_text)
        next_start = len(body_text)
        for future_header in headers[index + 1 :]:
            candidate = body_text.find(future_header, content_start)
            if candidate != -1 and candidate < next_start:
                next_start = candidate
                break
        section_text = clean_text(body_text[content_start:next_start])
        sections.append({"header": header_text, "body": section_text})
        cursor = content_start

    return sections


def is_question_header(text: str) -> bool:
    cleaned = clean_text(text)
    lowered = cleaned.lower()
    if not cleaned:
        return False
    if cleaned.endswith("?"):
        return True
    if lowered.startswith(("q:", "q.", "question:")):
        return True
    return any(lowered.startswith(prefix + " ") for prefix in QUESTION_PREFIXES)


def is_step_header(text: str, position: int | None = None) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    patterns = [
        r"^(?:step|stage|part)\s+\d+([:.)-]|\s)",
        r"^\d+[.)-]\s+\S+",
        r"^[ivx]+[.)-]\s+\S+",
    ]
    if any(re.match(pattern, cleaned, flags=re.IGNORECASE) for pattern in patterns):
        return True
    if position is not None and cleaned.lower().startswith("step "):
        return True
    return False


def detect_type(payload: dict[str, Any], override: Optional[str]) -> str:
    if override:
        return override

    headers = [clean_text(header["text"]) for header in payload["headers"]]
    question_count = sum(1 for header in headers if is_question_header(header))
    step_count = sum(1 for index, header in enumerate(headers, start=1) if is_step_header(header, index))

    if question_count >= 2:
        return "faq"
    if step_count >= 2:
        return "howto"
    return "article"


def build_faq_pairs(payload: dict[str, Any]) -> list[dict[str, str]]:
    sections = find_sections(payload)
    qa_pairs: list[dict[str, str]] = []
    for section in sections:
        if is_question_header(section["header"]) and section["body"]:
            qa_pairs.append({"question": section["header"], "answer": section["body"]})
    return qa_pairs


def build_steps(payload: dict[str, Any]) -> list[dict[str, str]]:
    sections = find_sections(payload)
    steps: list[dict[str, str]] = []
    for index, section in enumerate(sections, start=1):
        if is_step_header(section["header"], index):
            step_text = section["body"] or f"Follow the step described by {section['header']}."
            steps.append({"name": section["header"], "text": step_text})
    return steps


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return base if base is not None else overlay


def parse_existing_schema(raw_schema: Optional[str]) -> Any:
    if not raw_schema:
        return None

    cleaned = raw_schema.strip()
    if not cleaned:
        return None

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
        parsed_blocks = []
        for block in blocks:
            try:
                parsed_blocks.append(json.loads(block))
            except json.JSONDecodeError:
                continue
        if not parsed_blocks:
            return None
        if len(parsed_blocks) == 1:
            return parsed_blocks[0]
        return parsed_blocks


def generate_schema(payload: dict[str, Any], schema_type: str) -> tuple[dict[str, Any], list[str], Any]:
    notes: list[str] = []
    if schema_type == "faq":
        qa_pairs = build_faq_pairs(payload)
        if not qa_pairs:
            notes.append("no FAQ-style question headers were found; emitted an empty FAQPage mainEntity array")
        generated = build_faq_schema(payload, qa_pairs)
    elif schema_type == "howto":
        steps = build_steps(payload)
        if not steps:
            notes.append("no numbered step headers were found; emitted an empty HowTo step array")
        generated = build_howto_schema(payload, steps)
    else:
        generated = build_article_schema(payload, summarize_article_body(payload["body_text"], payload["title"]))
        if payload.get("meta_description") is None:
            notes.append("no meta description available, description omitted")
        if payload.get("published_date") is None:
            notes.append("no published date available, datePublished omitted")
        if payload.get("updated_date") is None:
            notes.append("no updated date available, dateModified omitted")

    existing = parse_existing_schema(payload.get("existing_schema"))
    suggested_merge = None
    if existing is not None:
        notes.append("existing schema found, suggested_merge included")
        if isinstance(existing, dict):
            suggested_merge = deep_merge(existing, generated)
        else:
            suggested_merge = {
                "existing_schema": existing,
                "generated_schema": generated,
            }

    return generated, notes, suggested_merge


def choose_ready_to_paste_schema(generated: dict[str, Any], suggested_merge: Any) -> dict[str, Any]:
    if isinstance(suggested_merge, dict) and any(key in suggested_merge for key in ("@context", "@graph", "@type")):
        return suggested_merge
    return generated


def write_output(result: dict[str, Any], title: str) -> Path:
    output_dir = Path.cwd() / "output" / "schema"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slugify(title)}.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 3 schema markup generator for AI-citation optimization.")
    parser.add_argument("--input", required=True, help="Path to a Phase 1 output JSON file.")
    parser.add_argument("--type", choices=("article", "faq", "howto"), help="Optional schema type override.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = load_input(args.input)
        detected_type = detect_type(payload, args.type)
        schema_jsonld, notes, suggested_merge = generate_schema(payload, detected_type)

        json.dumps(schema_jsonld, ensure_ascii=False)
        ready_to_paste_schema = choose_ready_to_paste_schema(schema_jsonld, suggested_merge)
        ready_to_paste = json.dumps(ready_to_paste_schema, indent=2, ensure_ascii=False)
        json.loads(ready_to_paste)

        result: dict[str, Any] = {
            "source": payload["source"],
            "detected_type": detected_type,
            "schema_jsonld": schema_jsonld,
            "ready_to_paste": f'<script type="application/ld+json">{ready_to_paste}</script>',
            "notes": notes,
        }
        if suggested_merge is not None:
            result["suggested_merge"] = suggested_merge

        write_output(result, str(payload["title"]))
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except GeneratorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while generating schema.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())