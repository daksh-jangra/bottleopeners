"""Content brief generator: from a target question, a plan to win the citation.

Rule-based and deterministic - no model call, no API credit. The brief is built
from the same six citation-readiness factors the analyzer scores on, so "how to
win it" maps one-to-one onto what Analyze and Teardown later check. That closes
the loop: Niche Explorer finds the question -> this brief says how to win it ->
Teardown verifies you did.

The question's intent (how-to, comparison, best-of, pricing, definition, or a
plain question) shapes the recommended title, schema type, section outline, and
target length; the must-include list is the six factors, ranked so the ones that
matter most for that intent come first.
"""

from __future__ import annotations

import re
from typing import Any

from rubric import FACTOR_LABELS, RECOMMENDATIONS

# Each intent: a detector keyword set, a human label, the Schema.org type that
# fits, a rough target length, extra section headings, and which rubric factors
# to surface first (beyond answer-first, which always leads).
INTENTS: list[dict[str, Any]] = [
    {
        "key": "comparison",
        "label": "Comparison / vs",
        "schema": "Article",
        "words": 900,
        "sections": ["Quick verdict", "Side-by-side comparison table", "When to pick each", "Bottom line"],
        "emphasis": ["list_table_presence", "factual_specificity"],
        "keywords": ["vs", "versus", "compare", "comparison", "difference between"],
    },
    {
        "key": "pricing",
        "label": "Pricing / cost",
        "schema": "Article",
        "words": 600,
        "sections": ["Short answer with the number", "Price breakdown table", "What changes the price", "How to save"],
        "emphasis": ["factual_specificity", "list_table_presence"],
        "keywords": ["price", "pricing", "cost", "how much", "fee", "cheap", "expensive"],
    },
    {
        "key": "how_to",
        "label": "How-to / process",
        "schema": "HowTo",
        "words": 800,
        "sections": ["What you'll need", "Step-by-step instructions", "Common mistakes", "FAQ"],
        "emphasis": ["list_table_presence", "header_quality"],
        "keywords": ["how to", "how do", "how can", "steps", "guide", "tutorial", "set up", "setup", "install"],
    },
    {
        "key": "best",
        "label": "Best-of / recommendation",
        "schema": "Article",
        "words": 1000,
        "sections": ["Top pick up front", "Ranked options table", "How we chose", "FAQ"],
        "emphasis": ["list_table_presence", "byline_authority"],
        "keywords": ["best", "top", "review", "recommended"],
    },
    {
        "key": "definition",
        "label": "Definition / explainer",
        "schema": "Article",
        "words": 500,
        "sections": ["One-sentence definition", "Why it matters", "Example", "Related terms"],
        "emphasis": ["factual_specificity", "header_quality"],
        "keywords": ["what is", "what are", "what's", "define", "definition", "meaning of"],
    },
]

# Fallback when nothing above matches: a plain question, answered FAQ-style.
DEFAULT_INTENT: dict[str, Any] = {
    "key": "question",
    "label": "Direct question",
    "schema": "FAQPage",
    "words": 600,
    "sections": ["Direct answer", "Supporting detail", "Related questions"],
    "emphasis": ["factual_specificity"],
}


def _mentions(question_lower: str, keyword: str) -> bool:
    """Whole-word/phrase match, so 'coffee' never trips the 'fee' keyword."""
    return re.search(rf"\b{re.escape(keyword)}\b", question_lower) is not None


def detect_intent(question: str) -> dict[str, Any]:
    """Match a question to the first intent whose keywords appear in it."""
    q = question.lower().strip()
    for intent in INTENTS:
        if any(_mentions(q, kw) for kw in intent["keywords"]):
            return intent
    return DEFAULT_INTENT


def _clean_question(question: str) -> str:
    """Collapse whitespace and drop a trailing question mark for reuse in a title."""
    return re.sub(r"\s+", " ", question).strip().rstrip("?").strip()


def _title_case(text: str) -> str:
    """Sentence-style title: capitalize the first letter, leave the rest as written."""
    text = text.strip()
    return text[:1].upper() + text[1:] if text else text


def suggested_title(question: str, intent: dict[str, Any]) -> str:
    """A headline shaped around the question and its intent."""
    core = _clean_question(question)
    if not core:
        return "Untitled brief"
    base = _title_case(core)
    suffix = {
        "how_to": ": A Step-by-Step Guide",
        "comparison": ": Which Should You Choose?",
        "pricing": ": Real Costs, Explained",
        "best": " (Ranked & Compared)",
        "definition": ", Explained Simply",
    }.get(intent["key"], "")
    return base + suffix


def _must_include(intent: dict[str, Any]) -> list[dict[str, str]]:
    """The six rubric factors as brief requirements, emphasis factors first.

    Answer-first always leads (it's what an answer engine quotes), then the
    intent's emphasis factors, then the remainder - so every brief is a complete
    plan against the same rubric Teardown will grade.
    """
    order: list[str] = ["answer_first_structure"]
    for key in intent["emphasis"]:
        if key not in order:
            order.append(key)
    for key in FACTOR_LABELS:
        if key not in order:
            order.append(key)
    return [
        {"factor": key, "label": FACTOR_LABELS[key], "why": RECOMMENDATIONS[key]}
        for key in order
    ]


def build_brief(question: str) -> dict[str, Any]:
    """Turn a target question into a rule-based content brief.

    Pure and deterministic: the same question always yields the same brief.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("Enter a target question.")

    intent = detect_intent(question)
    core = _clean_question(question)
    outline = ["Answer-first opening"] + list(intent["sections"])

    return {
        "question": question,
        "intent": intent["key"],
        "intent_label": intent["label"],
        "suggested_title": suggested_title(question, intent),
        "schema_type": intent["schema"],
        "target_word_count": intent["words"],
        "answer_first": (
            f"Open with a direct, 40-60 word answer to “{core}?” in the very first "
            "paragraph - the sentence you'd want an AI to quote verbatim."
        ),
        "outline": outline,
        "must_include": _must_include(intent),
    }
