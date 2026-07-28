"""AEO Audit: a pass/fail checklist view over the citation-readiness signals.

Where the rubric (Phase 5) rolls everything into a single 0-100 score, this
module presents the same underlying signals as a category-grouped checklist of
discrete checks - each one pass / warn / fail - the way a site auditor would.

It reuses the Phase 2 analysis for content-quality checks and adds a handful of
cheap technical checks read straight from the fetched page (Open Graph tags,
canonical tag, HTTPS + HSTS, JS-free rendering). No new dependencies, no model
calls, no extra network requests beyond the single page fetch the caller does.

Statuses:
  pass  - the check is satisfied
  warn  - present but weak / incomplete
  fail  - missing or clearly inadequate
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

PASS, WARN, FAIL = "pass", "warn", "fail"

# Core Open Graph tags that matter for how a link is represented when shared /
# ingested. url and type are nice-to-have; the trio below is what we require.
OG_REQUIRED = ("og:title", "og:description", "og:image")
OG_OPTIONAL = ("og:url", "og:type")


def _row(category: str, check: str, status: str, detail: str,
         fix: Optional[str] = None) -> dict[str, Any]:
    """A single audit check. `fix` names the tab that can fix it when the check
    is not passing ("rewrite" or "schema"), or None for advice-only checks
    (HTTPS, canonical, etc.) the tool can't auto-fix."""
    return {"category": category, "check": check, "status": status,
            "detail": detail, "fix": fix}


def _ratio_status(score: int, max_score: int) -> str:
    """Map a rubric factor's score to a pass/warn/fail status.

    Thresholds mirror the dashboard's bar colors (75% green, 40% amber).
    """
    if max_score <= 0:
        return FAIL
    pct = score / max_score
    if pct >= 0.75:
        return PASS
    if pct >= 0.40:
        return WARN
    return FAIL


# The rubric's "issues" list mixes praise ("Author cues were detected") with
# real problems. On a warn/fail row we want the problem, so prefer an issue that
# reads like a gap; fall back to the factor's fix hint if every issue is praise.
NEGATIVE_MARKERS = (
    "no ", "not ", "missing", "vague", "add ", "lacks", "too ", "jump",
    "duplicate", "only ", "reduce", "verify", "repeats", "generic",
    "delayed", "buildup", "hedging", "narrative", "short and", "closely", "decay",
)


def _negative_issue(section: dict[str, Any]) -> Optional[str]:
    for issue in section.get("issues", []):
        if isinstance(issue, str) and any(m in issue.lower() for m in NEGATIVE_MARKERS):
            return issue
    return None


# --- Structure & Schema -----------------------------------------------------

def _extract_schema_types(schema_text: Optional[str]) -> list[str]:
    """Pull every @type value out of one or more JSON-LD blocks.

    Regex-based so a malformed block doesn't lose the whole page's types.
    Handles both `"@type": "X"` and `"@type": ["X", "Y"]`.
    """
    if not schema_text:
        return []
    types: list[str] = []
    for match in re.finditer(r'"@type"\s*:\s*(\[[^\]]*\]|"[^"]*")', schema_text):
        value = match.group(1)
        if value.startswith("["):
            types.extend(re.findall(r'"([^"]+)"', value))
        else:
            types.append(value.strip('"'))
    seen: list[str] = []
    for t in types:
        if t and t not in seen:
            seen.append(t)
    return seen


def _check_schema(payload: dict[str, Any]) -> dict[str, Any]:
    types = _extract_schema_types(payload.get("existing_schema"))
    if not types:
        return _row("Structure & Schema", "Schema.org markup", FAIL,
                    "No JSON-LD structured data found on the page.", fix="schema")
    label = ", ".join(types[:6]) + ("…" if len(types) > 6 else "")
    plural = "type" if len(types) == 1 else "types"
    return _row("Structure & Schema", "Schema.org markup", PASS,
                f"{len(types)} {plural}: {label}", fix="schema")


def _check_faq(payload: dict[str, Any]) -> dict[str, Any]:
    schema_text = payload.get("existing_schema") or ""
    types = _extract_schema_types(schema_text)
    has_faq = any(t in ("FAQPage", "QAPage", "Question") for t in types)
    if not has_faq:
        return _row("Structure & Schema", "FAQ schema", FAIL,
                    "No FAQPage / Question markup - add Q&A schema for answer engines.", fix="schema")
    questions = len(re.findall(r'"@type"\s*:\s*"Question"', schema_text))
    detail = f"{questions} question{'' if questions == 1 else 's'}" if questions else "FAQ markup present"
    return _row("Structure & Schema", "FAQ schema", PASS, detail, fix="schema")


def _og_map(soup: BeautifulSoup) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop.startswith("og:") and tag.get("content", "").strip():
            tags[prop] = tag["content"].strip()
    return tags


def _check_open_graph(soup: BeautifulSoup) -> dict[str, Any]:
    tags = _og_map(soup)
    missing_required = [t for t in OG_REQUIRED if t not in tags]
    missing_optional = [t for t in OG_OPTIONAL if t not in tags]
    if not tags:
        return _row("Structure & Schema", "Open Graph tags", FAIL,
                    "No Open Graph tags found.")
    if missing_required:
        return _row("Structure & Schema", "Open Graph tags", WARN,
                    "Missing " + ", ".join(missing_required + missing_optional) + ".")
    if missing_optional:
        return _row("Structure & Schema", "Open Graph tags", PASS,
                    "Core tags present; optional " + ", ".join(missing_optional) + " missing.")
    return _row("Structure & Schema", "Open Graph tags", PASS, "All core + optional tags present.")


def _check_canonical(soup: BeautifulSoup) -> dict[str, Any]:
    link = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    href = link.get("href", "").strip() if link else ""
    if href:
        return _row("Structure & Schema", "Canonical tag", PASS, href)
    return _row("Structure & Schema", "Canonical tag", FAIL,
                "No <link rel=\"canonical\"> - add one to avoid duplicate-content ambiguity.")


# --- Content Quality --------------------------------------------------------

def _check_meta_description(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta_description")
    if not meta:
        return _row("Content Quality", "Meta description", FAIL,
                    "No meta description - add a concise page summary.", fix="rewrite")
    length = len(meta)
    if 50 <= length <= 170:
        return _row("Content Quality", "Meta description", PASS, f"{length} characters.")
    hint = "too short" if length < 50 else "too long"
    return _row("Content Quality", "Meta description", WARN,
                f"{length} characters ({hint}); aim for 50-170.", fix="rewrite")


# factor, label, pass detail, fix hint (used when warn/fail and no problem issue).
CONTENT_FACTORS = [
    ("answer_first_structure", "BLUF-style content",
     "Opens with a direct, quotable answer.", "Lead with a direct answer in the first sentence."),
    ("header_quality", "Heading hierarchy",
     "Headings are specific and well-nested.", "Use specific, question-style headings with a clear H1."),
    ("list_table_presence", "Lists & tables",
     "Uses lists/tables AI can extract.", "Add numbered lists or tables so points are extractable."),
    ("factual_specificity", "Factual specificity",
     "Dense with concrete, citable facts.", "Add concrete numbers, dates, and named entities."),
    ("byline_authority", "Author & publisher signals",
     "Author/publisher signals present.", "Add a visible author byline and a publisher/meta description."),
    ("recency_signals", "Freshness / dates",
     "Has a published or updated date.", "Add the page's real published or last-updated date."),
]


def _content_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    breakdown = analysis.get("breakdown", {})
    rows: list[dict[str, Any]] = []
    for factor, label, pass_detail, fix_detail in CONTENT_FACTORS:
        section = breakdown.get(factor)
        if not isinstance(section, dict):
            continue
        score, max_score = int(section["score"]), int(section["max"])
        status = _ratio_status(score, max_score)
        if status == PASS:
            detail = pass_detail
        else:
            detail = _negative_issue(section) or fix_detail
        detail = f"{detail} ({score}/{max_score} pts)"
        # Rewrite can fix every content factor except freshness — the tool
        # never invents a date, so that one stays advice-only.
        fix = None if factor == "recency_signals" else "rewrite"
        rows.append(_row("Content Quality", label, status, detail, fix=fix))
    return rows


# --- Technical --------------------------------------------------------------

def _check_https(final_url: str) -> dict[str, Any]:
    scheme = urlparse(final_url).scheme.lower()
    if scheme == "https":
        return _row("Technical", "HTTPS", PASS, "Served over HTTPS.")
    return _row("Technical", "HTTPS", FAIL, f"Served over {scheme or 'http'} - not encrypted.")


def _check_hsts(final_url: str, headers: dict[str, str]) -> dict[str, Any]:
    # Headers from requests are case-insensitive, but normalize defensively.
    value = ""
    for key, val in headers.items():
        if key.lower() == "strict-transport-security":
            value = val
            break
    if value:
        return _row("Technical", "HSTS", PASS, "Strict-Transport-Security header set.")
    if urlparse(final_url).scheme.lower() != "https":
        return _row("Technical", "HSTS", FAIL, "No HSTS (page is not HTTPS).")
    return _row("Technical", "HSTS", WARN,
                "No Strict-Transport-Security header - add one to enforce HTTPS.")


# --- Server-Side Rendering --------------------------------------------------

def _check_js_free(payload: dict[str, Any]) -> dict[str, Any]:
    """The page was fetched without executing JS. Readable body text therefore
    means the content renders server-side; near-empty text implies it depends
    on client-side JS an answer engine's crawler may not run."""
    words = int(payload.get("word_count") or 0)
    if words >= 100:
        return _row("Server-Side Rendering", "JS-free rendering", PASS,
                    f"{words} words readable without JavaScript.")
    if words >= 30:
        return _row("Server-Side Rendering", "JS-free rendering", WARN,
                    f"Only {words} words in raw HTML - some content may need JavaScript.")
    return _row("Server-Side Rendering", "JS-free rendering", FAIL,
                f"Only {words} words in raw HTML - content likely requires JavaScript.")


# --- Assembly ---------------------------------------------------------------

CATEGORY_ORDER = ["Structure & Schema", "Content Quality", "Technical", "Server-Side Rendering"]


def build_audit(
    payload: dict[str, Any],
    analysis: dict[str, Any],
    html: str,
    final_url: str,
    headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Return the full AEO audit for a single page.

    payload / analysis come from ingest + analyzer; html and final_url/headers
    come from the same fetch the caller already performed.
    """
    headers = headers or {}
    soup = BeautifulSoup(html or "", "html.parser")

    checks: list[dict[str, Any]] = [
        _check_schema(payload),
        _check_faq(payload),
        _check_open_graph(soup),
        _check_canonical(soup),
        _check_meta_description(payload),
        *_content_rows(analysis),
        _check_https(final_url),
        _check_hsts(final_url, headers),
        _check_js_free(payload),
    ]

    summary = {PASS: 0, WARN: 0, FAIL: 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1

    categories = []
    for name in CATEGORY_ORDER:
        rows = [c for c in checks if c["category"] == name]
        if rows:
            categories.append({"name": name, "checks": rows})

    return {
        "source": payload.get("source", final_url),
        "title": payload.get("title"),
        "summary": {"pass": summary[PASS], "warn": summary[WARN], "fail": summary[FAIL],
                    "total": len(checks)},
        "categories": categories,
    }
