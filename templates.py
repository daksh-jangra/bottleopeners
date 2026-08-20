"""Schema.org JSON-LD template builders for phase 3."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

SCHEMA_CONTEXT = "https://schema.org"

# Title separators brands use to append their name, e.g. "Page Title - Faber India".
_TITLE_SEPARATORS = ("—", "–", "|", "·", " - ")


def _site_host(payload: dict[str, Any]) -> str:
    """Bare host for the page, tolerating a source that omits the scheme."""
    raw = str(payload.get("source") or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "//" in raw else "https://" + raw)
    host = parsed.netloc or ""
    return host[4:] if host.startswith("www.") else host


def _brand_name(payload: dict[str, Any]) -> str | None:
    """Best-effort publisher name, read only from the page's own title or domain.

    Never invents a brand: prefers the short trailing segment a title appends
    after a separator ("... - Faber India"), and otherwise falls back to the
    bare host. Returns None when the page gives us nothing to go on.
    """
    title = str(payload.get("title") or "").strip()
    for sep in _TITLE_SEPARATORS:
        if sep in title:
            candidate = title.split(sep)[-1].strip(" -–—|·")
            if candidate and len(candidate.split()) <= 6:
                return candidate
    return _site_host(payload) or None


def _attribution(payload: dict[str, Any]) -> dict[str, Any]:
    """`author` and `publisher` blocks derived only from what the page states.

    The publisher is the site that owns the page, which is always known. The
    author is the person the page declares; when it names none we attribute the
    work to the organization rather than fabricating a byline.
    """
    brand = _brand_name(payload)
    if not brand:
        return {}
    blocks: dict[str, Any] = {}
    author = payload.get("author")
    if isinstance(author, str) and author.strip():
        blocks["author"] = {"@type": "Person", "name": author.strip()}
    else:
        blocks["author"] = {"@type": "Organization", "name": brand}
    publisher: dict[str, Any] = {"@type": "Organization", "name": brand}
    host = _site_host(payload)
    if host:
        publisher["url"] = f"https://{host}/"
    blocks["publisher"] = publisher
    return blocks


def build_article_schema(payload: dict[str, Any], article_body: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "Article",
        "headline": payload["title"],
        "wordCount": payload["word_count"],
        "articleBody": article_body,
    }
    schema.update(_attribution(payload))
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
    schema.update(_attribution(payload))
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
    schema.update(_attribution(payload))
    if payload.get("meta_description"):
        schema["description"] = payload["meta_description"]
    return schema