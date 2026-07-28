"""Phase 1 content ingestion for AI-citation optimization.

Usage:
  python ingest.py --url https://example.com/article
  python ingest.py --file ./sample.html
  python ingest.py --text ./notes.md

Each run accepts exactly one input source and writes normalized JSON to
./output/<slugified-title>.json while also printing the same JSON to stdout.

Examples:
  python ingest.py --url https://example.com/post
  python ingest.py --file ./downloads/page.html
  python ingest.py --text ./docs/draft.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

DEFAULT_TIMEOUT_SECONDS = 15
# Note: <header> is deliberately NOT stripped — articles often wrap their H1 in
# one. Page-level headers are excluded instead by preferring the <main>/<article>
# root, plus the BOILERPLATE_HEADINGS filter for stray "Log in"-style labels.
UNWANTED_TAGS = ("script", "style", "noscript", "template", "nav", "footer", "aside", "form")

# Section headings that are page furniture, not article content. Real sites bury
# these inside the main content area, so tag-stripping alone won't remove them.
BOILERPLATE_HEADINGS = frozenset({
    "references", "related", "related articles", "you might also like",
    "about this article", "see also", "external links", "further reading",
    "sources", "citations", "log in", "sign up", "sign in", "navigation menu",
    "table of contents", "share", "comments", "advertisement",
    "trending articles", "trending", "quizzes & games", "quizzes",
    "reader success stories", "did this article help you?",
    "featured articles", "popular categories", "newsletter",
    "featured videos", "watch articles", "video", "videos",
})


class IngestError(Exception):
    pass


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "output"


def parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed.date().isoformat()


def first_nonempty(values: Iterable[Optional[str]]) -> Optional[str]:
    for value in values:
        if value:
            cleaned = normalize_whitespace(value)
            if cleaned:
                return cleaned
    return None


def extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
    selectors = [
        ("meta", {"name": "description"}),
        ("meta", {"property": "og:description"}),
        ("meta", {"name": "twitter:description"}),
    ]
    for tag_name, attrs in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get("content"):
            return normalize_whitespace(tag["content"])
    return None


def extract_headers(soup: BeautifulSoup) -> list[dict[str, object]]:
    headers = []
    for header in soup.find_all(["h1", "h2", "h3"]):
        text = normalize_whitespace(header.get_text(" ", strip=True))
        if not text:
            continue
        if text.lower() in BOILERPLATE_HEADINGS:
            continue
        level = int(header.name[1])
        headers.append({"level": level, "text": text})
    return headers


def extract_schema_blocks(soup: BeautifulSoup) -> Optional[str]:
    blocks = []
    for tag in soup.find_all("script", attrs={"type": lambda value: value and value.lower() == "application/ld+json"}):
        raw = tag.string or tag.get_text(strip=True)
        cleaned = raw.strip() if raw else ""
        if cleaned:
            blocks.append(cleaned)
    if not blocks:
        return None
    return "\n\n".join(blocks)


def remove_unwanted_elements(soup: BeautifulSoup) -> None:
    for selector in UNWANTED_TAGS:
        for tag in soup.find_all(selector):
            tag.decompose()


def count_html_structure(root) -> tuple[int, int]:
    """Count list and table elements before their markup is flattened into text.

    get_text() discards <ul>/<ol>/<table> structure, so later phases cannot
    recover it from body_text. Counting here keeps the signal format-independent.
    """
    list_count = len(root.find_all(["ul", "ol"]))
    table_count = len(root.find_all("table"))
    return list_count, table_count


def extract_visible_dates(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    published_candidates = [
        soup.find("meta", attrs={"property": "article:published_time"}),
        soup.find("meta", attrs={"property": "og:published_time"}),
        soup.find("meta", attrs={"name": "pubdate"}),
        soup.find("meta", attrs={"name": "publish-date"}),
        soup.find("meta", attrs={"name": "date"}),
        soup.find("meta", attrs={"itemprop": "datePublished"}),
    ]
    updated_candidates = [
        soup.find("meta", attrs={"property": "article:modified_time"}),
        soup.find("meta", attrs={"property": "og:updated_time"}),
        soup.find("meta", attrs={"name": "last-modified"}),
        soup.find("meta", attrs={"itemprop": "dateModified"}),
    ]

    time_tags = soup.find_all("time")
    for time_tag in time_tags:
        if time_tag.get("datetime"):
            published_candidates.append(time_tag)
            updated_candidates.append(time_tag)
        elif time_tag.get_text(strip=True):
            published_candidates.append(time_tag)
            updated_candidates.append(time_tag)

    def get_candidate_value(tag) -> Optional[str]:
        if not tag:
            return None
        if tag.name == "meta":
            return tag.get("content")
        if tag.name == "time":
            return tag.get("datetime") or tag.get_text(" ", strip=True)
        return None

    published_date = None
    for candidate in published_candidates:
        published_date = parse_date(get_candidate_value(candidate))
        if published_date:
            break

    updated_date = None
    for candidate in updated_candidates:
        updated_date = parse_date(get_candidate_value(candidate))
        if updated_date:
            break

    return published_date, updated_date


# Many pages show a date as visible text ("Last Updated: March 10, 2025")
# rather than in a <meta>/<time> tag. Capture just the date substring after a
# published/updated label so it can be parsed on its own.
_DATE_SUBSTRING = (
    r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"  # March 10, 2025
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"      # 10 March 2025
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"           # 03/10/2025
    r"|\d{4}-\d{2}-\d{2})"                        # 2025-03-10
)
UPDATED_TEXT_RE = re.compile(r"(?:last\s+updated|updated)(?:\s+on)?\s*:?\s*" + _DATE_SUBSTRING, re.IGNORECASE)
PUBLISHED_TEXT_RE = re.compile(r"(?:published|posted|first\s+published)(?:\s+on)?\s*:?\s*" + _DATE_SUBSTRING, re.IGNORECASE)


def extract_labeled_dates(text: str) -> tuple[Optional[str], Optional[str]]:
    """Find published/updated dates written as visible text in the page body."""
    published = None
    updated = None
    match = PUBLISHED_TEXT_RE.search(text)
    if match:
        published = parse_date(match.group(1))
    match = UPDATED_TEXT_RE.search(text)
    if match:
        updated = parse_date(match.group(1))
    return published, updated


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _handrolled_root(html: str):
    body_soup = BeautifulSoup(html, "html.parser")
    remove_unwanted_elements(body_soup)
    return body_soup.find("main") or body_soup.find("article") or body_soup.body or body_soup


def _trafilatura_root(html: str, raw_soup: BeautifulSoup, title: str):
    """Trafilatura-isolated article, or None if unavailable/empty."""
    try:
        import trafilatura
        article_html = trafilatura.extract(
            html, output_format="html", include_tables=True, include_comments=False
        )
    except Exception:
        return None
    if not article_html:
        return None
    soup = BeautifulSoup(article_html, "html.parser")
    # Re-inject the page's real H1 only if trafilatura didn't keep one, so a page
    # that already has its heading isn't given a duplicate.
    if not soup.find("h1"):
        h1_tag = raw_soup.find("h1")
        heading = normalize_whitespace(h1_tag.get_text(" ", strip=True)) if h1_tag else title
        if heading and heading != "Untitled":
            h1 = soup.new_tag("h1")
            h1.string = heading
            soup.insert(0, h1)
    return soup


def extract_article_root(html: str, raw_soup: BeautifulSoup, title: str):
    """Isolate the article body. Use trafilatura only when it clearly strips
    chrome (much less text than hand-rolled cleaning) and keeps enough content;
    otherwise the hand-rolled root is more reliable (and dodges trafilatura's
    occasional duplication on small/simple pages)."""
    hand_root = _handrolled_root(html)
    traf_root = _trafilatura_root(html, raw_soup, title)
    if traf_root is not None:
        hand_words = len(normalize_whitespace(hand_root.get_text(" ", strip=True)).split())
        traf_words = len(normalize_whitespace(traf_root.get_text(" ", strip=True)).split())
        if traf_words >= 100 and traf_words < 0.9 * hand_words:
            return traf_root
    return hand_root


def extract_html_payload(html: str, source: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    title = first_nonempty([
        soup.title.get_text(" ", strip=True) if soup.title else None,
        soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None,
        Path(urlparse(source).path).stem if urlparse(source).scheme else None,
    ]) or "Untitled"
    meta_description = extract_meta_description(soup)
    existing_schema = extract_schema_blocks(soup)

    # Isolate the real article, then read headers/structure/text from just that.
    content_root = extract_article_root(html, soup, title)
    headers = extract_headers(content_root)
    list_count, table_count = count_html_structure(content_root)
    body_text = normalize_whitespace(content_root.get_text(" ", strip=True))
    if not body_text:
        raise IngestError("No readable body text was found in the HTML input.")

    published_date, updated_date = extract_visible_dates(soup)
    # Fall back to a date shown as visible text (e.g. wikiHow's "Last Updated:
    # March 10, 2025"). Read it from the raw page, since article extraction may
    # strip the byline line. Metadata still wins.
    if published_date is None or updated_date is None:
        raw_text = normalize_whitespace(soup.get_text(" ", strip=True))
        text_published, text_updated = extract_labeled_dates(raw_text)
        published_date = published_date or text_published
        updated_date = updated_date or text_updated

    return {
        "source": source,
        "title": title,
        "meta_description": meta_description,
        "headers": headers,
        "body_text": body_text,
        "existing_schema": existing_schema,
        "published_date": published_date,
        "updated_date": updated_date,
        "word_count": len(body_text.split()),
        "list_count": list_count,
        "table_count": table_count,
    }


MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MARKDOWN_HEADER_PATTERN = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
MARKDOWN_LIST_PATTERN = re.compile(r"^([*-]|\d+[.)])\s+")
MARKDOWN_EMPHASIS_PATTERN = re.compile(r"([*_`])")


def strip_markdown_line(line: str) -> str:
    line = MARKDOWN_LINK_PATTERN.sub(r"\1", line)
    line = MARKDOWN_LIST_PATTERN.sub("", line)
    line = line.lstrip("> ")
    line = MARKDOWN_EMPHASIS_PATTERN.sub("", line)
    return line.strip()


def count_markdown_structure(lines: Iterable[str]) -> tuple[int, int]:
    """Count list and table blocks from raw Markdown lines.

    Must run before strip_markdown_line(), which removes the list markers.
    A run of consecutive list items counts as one list; a run of consecutive
    pipe-table rows counts as one table.
    """
    list_count = 0
    table_count = 0
    in_list = False
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_table_row = stripped.count("|") >= 2
        is_list_item = not is_table_row and bool(MARKDOWN_LIST_PATTERN.match(stripped))

        if is_list_item and not in_list:
            list_count += 1
        if is_table_row and not in_table:
            table_count += 1

        in_list = is_list_item
        in_table = is_table_row

    return list_count, table_count


def extract_markdown_payload(text: str, source: str) -> dict[str, object]:
    lines = text.splitlines()
    list_count, table_count = count_markdown_structure(lines)
    headers = []
    body_lines = []
    title = None

    for line in lines:
        header_match = MARKDOWN_HEADER_PATTERN.match(line)
        if header_match:
            level = len(header_match.group(1))
            header_text = strip_markdown_line(header_match.group(2))
            if header_text:
                headers.append({"level": level, "text": header_text})
                if level == 1 and title is None:
                    title = header_text
            body_lines.append(header_text)
            continue
        cleaned = strip_markdown_line(line)
        if cleaned:
            body_lines.append(cleaned)

    if title is None:
        title = Path(source).stem.replace("_", " ").replace("-", " ").strip() or "Untitled"

    body_text = normalize_whitespace("\n".join(body_lines))
    if not body_text:
        raise IngestError("No readable body text was found in the text input.")

    return {
        "source": source,
        "title": title,
        "meta_description": None,
        "headers": headers,
        "body_text": body_text,
        "existing_schema": None,
        "published_date": None,
        "updated_date": None,
        "word_count": len(body_text.split()),
        "list_count": list_count,
        "table_count": table_count,
    }


def fetch_url_response(url: str) -> requests.Response:
    """Fetch a URL and return the full response.

    Callers that only need the HTML use fetch_url(); the audit needs the
    response object too (final URL after redirects, and headers like HSTS).
    """
    try:
        response = requests.get(
            url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ContentIngest/1.0)"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise IngestError(f"Unable to fetch URL {url}: {exc}") from None

    if not response.text.strip():
        raise IngestError(f"The URL returned empty content: {url}")
    return response


def fetch_url(url: str) -> str:
    return fetch_url_response(url).text


def load_file(path_str: str) -> str:
    path = Path(path_str)
    if not path.exists():
        raise IngestError(f"File not found: {path_str}")
    if not path.is_file():
        raise IngestError(f"Path is not a file: {path_str}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        raise IngestError(f"File is empty: {path_str}")
    return content


def write_output(payload: dict[str, object]) -> Path:
    output_dir = Path.cwd() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(str(payload['title']))}.json"
    output_path = output_dir / filename
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    if args.url:
        html = fetch_url(args.url)
        return extract_html_payload(html, args.url)
    if args.file:
        html = load_file(args.file)
        return extract_html_payload(html, args.file)
    if args.text:
        text = load_file(args.text)
        return extract_markdown_payload(text, args.text)
    raise IngestError("Provide exactly one of --url, --file, or --text.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1 content ingestion for AI-citation optimization.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Fetch and parse a live webpage.")
    group.add_argument("--file", help="Parse a local HTML file.")
    group.add_argument("--text", help="Parse a local Markdown or plain text file.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = build_payload(args)
        write_output(payload)
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except IngestError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while ingesting content.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
