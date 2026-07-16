"""Schema.org JSON-LD template builders for phase 3."""

from __future__ import annotations

from typing import Any

SCHEMA_CONTEXT = "https://schema.org"


def build_article_schema(payload: dict[str, Any], article_body: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "Article",
        "headline": payload["title"],
        "wordCount": payload["word_count"],
        "articleBody": article_body,
    }
    if payload.get("meta_description"):
        schema["description"] = payload["meta_description"]
    if payload.get("published_date"):
        schema["datePublished"] = payload["published_date"]
    if payload.get("updated_date"):
        schema["dateModified"] = payload["updated_date"]
    return schema


def build_faq_schema(payload: dict[str, Any], qa_pairs: list[dict[str, str]]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": pair["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": pair["answer"],
                },
            }
            for pair in qa_pairs
        ],
    }
    if payload.get("meta_description"):
        schema["description"] = payload["meta_description"]
    return schema


def build_howto_schema(payload: dict[str, Any], steps: list[dict[str, str]]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "HowTo",
        "name": payload["title"],
        "step": [
            {
                "@type": "HowToStep",
                "name": step["name"],
                "text": step["text"],
            }
            for step in steps
        ],
    }
    if payload.get("meta_description"):
        schema["description"] = payload["meta_description"]
    return schema