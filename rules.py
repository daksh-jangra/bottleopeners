"""Rule-based scoring for Phase 2 structure analysis.

Each scorer returns a JSON-serializable dictionary with:
- score: integer points awarded
- max: maximum points for the rule
- issues: actionable flags that explain the score
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

HEADER_MAX_SCORE = 20
ANSWER_FIRST_MAX_SCORE = 20
LIST_TABLE_MAX_SCORE = 15
FACTUAL_SPECIFICITY_MAX_SCORE = 15
BYLINE_AUTHORITY_MAX_SCORE = 15
RECENCY_MAX_SCORE = 15

RECENCY_HALF_LIFE_DAYS = 365.0

VAGUE_HEADER_PATTERNS = (
    r"^more info$",
    r"^more information$",
    r"^details?$",
    r"^section\s+\w+$",
    r"^introduction$",
    r"^overview$",
    r"^general information$",
    r"^extra details$",
    r"^learn more$",
    r"^conclusion$",
    r"^summary$",
)

HEADING_OPENERS = (
    "what ",
    "how ",
    "why ",
    "when ",
    "where ",
    "who ",
    "which ",
    "is ",
    "are ",
    "can ",
    "do ",
    "does ",
    "should ",
)

ANSWER_FIRST_HEDGES = (
    "in this article",
    "in this post",
    "in this guide",
    "we will",
    "we'll",
    "let's",
    "today",
    "this article explores",
    "this post explores",
    "this guide explains",
    "might",
    "may",
    "could",
    "generally",
    "often",
    "usually",
)

AUTHOR_CUES = (
    "written by",
    " by ",
    "author",
    "edited by",
    "contributor",
    "staff writer",
    "editorial team",
    "publisher",
)

ORG_CUES = (
    "inc",
    "llc",
    "ltd",
    "corp",
    "company",
    "university",
    "institute",
    "official",
    "news",
    "blog",
    "docs",
    "foundation",
    "press",
)

DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?\b",
        re.IGNORECASE,
    ),
)


def _component(score: int, max_score: int, issues: list[str]) -> dict[str, Any]:
    return {"score": max(0, min(max_score, int(score))), "max": max_score, "issues": issues}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _count_words(text: str) -> int:
    words = re.findall(r"\b\w+\b", text)
    return len(words)


def _is_vague_header(text: str) -> bool:
    lowered = text.lower().strip()
    for pattern in VAGUE_HEADER_PATTERNS:
        if re.match(pattern, lowered):
            return True
    return False


def _looks_descriptive_header(text: str) -> bool:
    lowered = text.lower().strip()
    if lowered.endswith("?"):
        return True
    return any(lowered.startswith(opener) for opener in HEADING_OPENERS) or len(lowered.split()) >= 3


def _detect_named_entity_phrases(text: str) -> list[str]:
    candidates = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    filtered: list[str] = []
    for candidate in candidates:
        if candidate not in filtered:
            filtered.append(candidate)
    return filtered


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def score_header_quality(payload: dict[str, Any]) -> dict[str, Any]:
    headers = payload["headers"]
    issues: list[str] = []

    if not headers:
        return _component(0, HEADER_MAX_SCORE, ["No H1-H3 headers were found; add structured headings to improve extractability."])

    score = HEADER_MAX_SCORE
    previous_level = 0
    has_h1 = False

    for index, header in enumerate(headers):
        text = str(header["text"])
        level = int(header["level"])

        if level == 1:
            has_h1 = True

        if _is_vague_header(text):
            issues.append(f"H{level} '{text}' is vague; use a more descriptive or question-style header.")
            score -= 4
        elif not _looks_descriptive_header(text):
            issues.append(f"H{level} '{text}' is short and generic; make it more specific for citation extraction.")
            score -= 2

        if previous_level and level > previous_level + 1:
            issues.append(f"Header order jumps from H{previous_level} to H{level}; keep nesting logical.")
            score -= 3
        if index > 0 and text.lower().strip() == str(headers[index - 1]["text"]).lower().strip():
            issues.append(f"Duplicate heading '{text}' repeats a previous header.")
            score -= 2
        previous_level = level

    if not has_h1:
        issues.append("No H1 header was found; add a top-level title-style heading.")
        score -= 4

    if len(headers) < 2:
        issues.append("Only one header was found; additional H2/H3 structure would improve sectioning.")
        score -= 2

    return _component(score, HEADER_MAX_SCORE, issues)


def score_answer_first_structure(payload: dict[str, Any]) -> dict[str, Any]:
    body_text = str(payload["body_text"])
    headers = payload["headers"]
    issues: list[str] = []

    sentences = _split_sentences(body_text)
    if not sentences:
        return _component(0, ANSWER_FIRST_MAX_SCORE, ["Body text has no readable sentences; add a direct lead sentence under each section."])

    score = ANSWER_FIRST_MAX_SCORE
    lead_sentences = sentences[:2]
    lead_text = " ".join(lead_sentences).lower()

    if any(phrase in lead_text for phrase in ANSWER_FIRST_HEDGES):
        issues.append("The opening sentences use narrative or hedging language instead of a direct answer.")
        score -= 6

    first_sentence = sentences[0]
    first_words = _count_words(first_sentence)
    if first_words > 30:
        issues.append("The first sentence is long and delayed; start with a shorter direct statement.")
        score -= 4
    elif first_words > 22:
        issues.append("The first sentence is somewhat long; tighten the opening for faster extraction.")
        score -= 2

    if not re.search(r"\b(is|are|means|refers to|includes|provides|contains|allows|requires|helps|supports)\b", lead_text):
        issues.append("The first two sentences do not clearly state the answer or definition up front.")
        score -= 4

    if re.match(r"^(in this article|this article|this post|this guide|today we|we will|let's|here we)", first_sentence.lower().strip()):
        issues.append("The opening is framed as setup rather than answer-first content.")
        score -= 4

    if len(headers) >= 2 and first_words > 18:
        issues.append("Sections appear to start with explanatory buildup; front-load the main point in the first sentence.")
        score -= 2

    return _component(score, ANSWER_FIRST_MAX_SCORE, issues)


def _structure_count(payload: dict[str, Any], field: str) -> int:
    """Read a Phase 1 structure count, tolerating payloads written before they existed."""
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def score_list_table_presence(payload: dict[str, Any]) -> dict[str, Any]:
    body_text = str(payload["body_text"])
    issues: list[str] = []

    score = 0
    numbered_items = len(re.findall(r"\b\d+[\).]\s+\w+", body_text))
    bullet_markers = len(re.findall(r"(?:^|\s)(?:[-*•]|\d+[\).])\s+", body_text))
    colon_pairs = len(re.findall(r"\b[A-Za-z][A-Za-z0-9 _-]{1,40}:\s+[^:;|]{2,40}", body_text))
    pipe_tables = body_text.count("|")
    semicolon_runs = body_text.count(";")
    compact_lines = len(re.findall(r"\b[A-Z][^.!?]{0,35}\b", body_text))

    # Phase 1 counts real <ul>/<ol>/<table> and Markdown blocks, so structure
    # survives even though body_text is flattened. Text heuristics remain as a
    # fallback for payloads ingested before these fields existed.
    list_count = _structure_count(payload, "list_count")
    table_count = _structure_count(payload, "table_count")

    evidence = 0
    if list_count >= 1 or numbered_items >= 2 or bullet_markers >= 2:
        evidence += 1
        issues.append("List structure was found; this helps AI extract discrete points.")
    if list_count >= 2 or (numbered_items >= 2 and bullet_markers >= 2):
        evidence += 1
        issues.append("Multiple distinct lists were found; keep list structure explicit.")
    if colon_pairs >= 3:
        evidence += 1
        issues.append("Key-value style patterns suggest a tabular or fact-dense layout.")
    if table_count >= 1 or pipe_tables >= 2:
        evidence += 1
        issues.append("Table structure was found; tables are highly citable by AI answers.")
    if semicolon_runs >= 3 and compact_lines >= 4:
        evidence += 1
        issues.append("Dense clause-separated formatting may indicate a list or table that is still readable.")

    if evidence == 0:
        issues.append("No strong list or table patterns were detected in the body text.")
    elif evidence == 1:
        score = 6
    elif evidence == 2:
        score = 10
    elif evidence == 3:
        score = 13
    else:
        score = LIST_TABLE_MAX_SCORE

    return _component(score, LIST_TABLE_MAX_SCORE, issues)


def score_factual_specificity(payload: dict[str, Any]) -> dict[str, Any]:
    body_text = str(payload["body_text"])
    word_count = int(payload["word_count"])
    issues: list[str] = []

    if word_count <= 0:
        return _component(0, FACTUAL_SPECIFICITY_MAX_SCORE, ["Word count is zero; factual specificity cannot be measured reliably."])

    numbers = re.findall(r"\b\d+(?:[.,:/-]\d+)?%?\b", body_text)
    dates = []
    for pattern in DATE_PATTERNS:
        dates.extend(pattern.findall(body_text))
    entities = _detect_named_entity_phrases(body_text)

    density_per_100 = ((len(numbers) + len(dates) + len(entities)) / word_count) * 100.0

    if len(numbers) == 0:
        issues.append("No numeric details were found; add concrete counts, measurements, or ranges where relevant.")
    if len(dates) == 0:
        issues.append("No date expressions were found; dated facts improve citation precision.")
    if len(entities) == 0:
        issues.append("No multi-word named entities were detected; specific organizations, products, or places improve specificity.")

    if density_per_100 < 0.5:
        score = 2
    elif density_per_100 < 1.0:
        score = 5
    elif density_per_100 < 2.0:
        score = 9
    elif density_per_100 < 3.0:
        score = 12
    else:
        score = FACTUAL_SPECIFICITY_MAX_SCORE

    if score < FACTUAL_SPECIFICITY_MAX_SCORE and len(numbers) + len(dates) + len(entities) >= 6:
        score = min(FACTUAL_SPECIFICITY_MAX_SCORE, score + 2)

    if score >= 12 and len(numbers) + len(dates) + len(entities) > 0:
        issues.append("The content includes multiple concrete facts that are easier to cite and verify.")

    return _component(score, FACTUAL_SPECIFICITY_MAX_SCORE, issues)


def score_byline_authority(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload["title"])
    meta_description = payload["meta_description"]
    existing_schema = payload["existing_schema"]
    body_text = str(payload["body_text"])
    issues: list[str] = []

    score = 0
    meta_text = str(meta_description or "")
    schema_text = str(existing_schema or "")
    combined = f"{title} {meta_text} {schema_text} {body_text[:500]}".lower()

    if any(cue in combined for cue in AUTHOR_CUES):
        score += 5
        issues.append("Author or editorial byline cues were detected.")
    else:
        issues.append("No author/byline cues were found in the title, meta description, body, or schema.")

    if any(cue in combined for cue in ORG_CUES):
        score += 4
        issues.append("Organization or publisher cues were detected in the title, meta description, or schema.")
    else:
        issues.append("No clear organization or publisher signal was found in the title or meta description.")

    meta_length = len(meta_text)
    if 50 <= meta_length <= 170 and meta_text:
        score += 4
    elif meta_text:
        issues.append("Meta description is present but not in a strong summary range; aim for a concise, specific description.")
        score += 2
    else:
        issues.append("No meta description was provided; add one that summarizes the page for citation contexts.")

    if meta_text and meta_text.lower().strip() not in title.lower().strip():
        score += 2
    elif meta_text:
        issues.append("Meta description closely repeats the title; add new useful detail instead of duplication.")

    if re.search(r"\b(author|byline|publisher|organization|publisher:|author:)\b", schema_text, re.IGNORECASE):
        score += 2

    if score == 0:
        score = 1 if meta_text else 0

    return _component(score, BYLINE_AUTHORITY_MAX_SCORE, issues)


def score_recency_signals(payload: dict[str, Any]) -> dict[str, Any]:
    published_date = _parse_date(payload["published_date"])
    updated_date = _parse_date(payload["updated_date"])
    issues: list[str] = []

    if not published_date and not updated_date:
        return _component(0, RECENCY_MAX_SCORE, ["No published or updated date was found; add a date signal for recency-aware citations."])

    effective_date = updated_date or published_date
    if published_date and updated_date and updated_date >= published_date:
        effective_date = updated_date

    assert effective_date is not None
    age_days = max(0, (date.today() - effective_date).days)
    decay_factor = math.exp(-(age_days / RECENCY_HALF_LIFE_DAYS) * math.log(2))
    score = int(round(RECENCY_MAX_SCORE * decay_factor))

    if updated_date:
        if published_date and updated_date < published_date:
            issues.append("Updated date is earlier than the published date; verify the dates are correct.")
        else:
            issues.append("Updated date is present, which improves freshness signals.")
    elif published_date:
        issues.append("Only a published date is present; adding an updated date would strengthen freshness signals.")

    if age_days > 365 * 3:
        issues.append("The content is several years old; recency score decays sharply for older material.")
    elif age_days > 365:
        issues.append("The content is over a year old; recency score is reduced.")

    return _component(score, RECENCY_MAX_SCORE, issues)
