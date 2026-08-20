"""Tests for the Phase 1 ingestion helpers in ingest.py.

Focuses on the parsing that has no coverage elsewhere - date extraction
(meta tags, <time>, and visible "Last Updated:" text), list/table counting for
both HTML and Markdown, the Markdown payload builder, and the SSRF guard
(scheme/host rejection, private-address blocking, and the returned pinned IP).
"""

import socket

import pytest

import ingest


# --- parse_date -------------------------------------------------------------

def test_parse_date_iso_passthrough():
    assert ingest.parse_date("2025-03-10") == "2025-03-10"


def test_parse_date_human_formats_normalize_to_iso():
    assert ingest.parse_date("March 10, 2025") == "2025-03-10"
    assert ingest.parse_date("10 March 2025") == "2025-03-10"


def test_parse_date_none_and_garbage():
    assert ingest.parse_date(None) is None
    assert ingest.parse_date("") is None
    assert ingest.parse_date("not a date at all") is None


# --- extract_labeled_dates (visible "Last Updated:" text) -------------------

def test_extract_labeled_dates_reads_published_and_updated():
    text = "Published on January 2, 2024. Last Updated: March 10, 2025."
    published, updated = ingest.extract_labeled_dates(text)
    assert published == "2024-01-02"
    assert updated == "2025-03-10"


def test_extract_labeled_dates_handles_slash_format():
    published, updated = ingest.extract_labeled_dates("Posted 03/10/2025")
    assert published == "2025-03-10"
    assert updated is None


def test_extract_labeled_dates_none_when_absent():
    assert ingest.extract_labeled_dates("just some prose with no dates") == (None, None)


# --- extract_visible_dates (meta tags + <time>) -----------------------------

def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_extract_visible_dates_from_meta():
    html = (
        '<meta property="article:published_time" content="2025-01-05T09:00:00Z">'
        '<meta property="article:modified_time" content="2025-02-06T09:00:00Z">'
    )
    published, updated = ingest.extract_visible_dates(_soup(html))
    assert published == "2025-01-05"
    assert updated == "2025-02-06"


def test_extract_visible_dates_from_time_tag():
    published, updated = ingest.extract_visible_dates(_soup('<time datetime="2025-07-04">July 4</time>'))
    assert published == "2025-07-04"


# --- HTML list/table counting -----------------------------------------------

def test_count_html_structure_counts_lists_and_tables():
    root = _soup("<div><ul><li>a</li></ul><ol><li>b</li></ol><table><tr><td>c</td></tr></table></div>")
    assert ingest.count_html_structure(root) == (2, 1)


def test_count_html_structure_zero_when_plain():
    assert ingest.count_html_structure(_soup("<div><p>plain</p></div>")) == (0, 0)


# --- Markdown structure counting --------------------------------------------

def test_count_markdown_structure_groups_consecutive_items():
    lines = ["- one", "- two", "- three", "", "text"]
    # three consecutive bullet lines are one list
    assert ingest.count_markdown_structure(lines) == (1, 0)


def test_count_markdown_structure_separate_lists_and_table():
    lines = ["- a", "", "- b", "", "| x | y |", "| 1 | 2 |"]
    # two lists separated by a blank line, one table (consecutive pipe rows)
    assert ingest.count_markdown_structure(lines) == (2, 1)


def test_count_markdown_structure_numbered_list():
    assert ingest.count_markdown_structure(["1. a", "2. b"]) == (1, 0)


# --- Markdown payload builder -----------------------------------------------

def test_extract_markdown_payload_title_headers_and_counts():
    text = (
        "# Coffee Guide\n"
        "Intro line.\n"
        "## Steps\n"
        "1. Grind\n"
        "2. Brew\n"
    )
    payload = ingest.extract_markdown_payload(text, "notes.md")
    assert payload["title"] == "Coffee Guide"
    assert {"level": 1, "text": "Coffee Guide"} in payload["headers"]
    assert {"level": 2, "text": "Steps"} in payload["headers"]
    assert payload["list_count"] == 1
    assert payload["word_count"] > 0
    # markdown payloads never carry HTML-only fields
    assert payload["existing_schema"] is None
    assert payload["meta_description"] is None


def test_extract_markdown_payload_title_falls_back_to_filename():
    payload = ingest.extract_markdown_payload("just body text, no heading", "my_draft-notes.md")
    assert payload["title"] == "my draft notes"


def test_extract_markdown_payload_empty_raises():
    with pytest.raises(ingest.IngestError):
        ingest.extract_markdown_payload("   \n\n  ", "empty.md")


# --- SSRF guard -------------------------------------------------------------

def test_guard_url_rejects_non_http_scheme():
    with pytest.raises(ingest.IngestError):
        ingest.guard_url("ftp://example.com/file")


def test_guard_url_rejects_missing_host():
    with pytest.raises(ingest.IngestError):
        ingest.guard_url("http:///no-host")


def test_guard_url_blocks_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(ingest.IngestError, match="private, loopback"):
        ingest.guard_url("http://evil.test/")


def test_guard_url_blocks_cloud_metadata_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))])
    with pytest.raises(ingest.IngestError):
        ingest.guard_url("http://metadata.test/")


def test_guard_url_blocks_when_any_resolved_ip_is_private(monkeypatch):
    # one public, one private -> refuse (a later resolution could pick the private one)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 0)),
        (2, 1, 6, "", ("10.0.0.5", 0)),
    ])
    with pytest.raises(ingest.IngestError):
        ingest.guard_url("http://mixed.test/")


def test_guard_url_returns_first_public_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 0)),
        (2, 1, 6, "", ("93.184.216.35", 0)),
    ])
    assert ingest.guard_url("http://safe.test/") == "93.184.216.34"


def test_guard_url_skips_resolution_in_dev_mode(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("getaddrinfo must not be called when private URLs are allowed")

    monkeypatch.setattr(ingest, "ALLOW_PRIVATE_URLS", True)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    # dev mode: accepted without resolution, and no IP to pin
    assert ingest.guard_url("http://localhost:8000/") is None


# --- extract_author ---------------------------------------------------------
#
# Byline scoring reads this field instead of guessing from prose, so what it
# does and does not find is what the byline_authority factor is worth.

def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_extract_author_from_meta_tag():
    s = _soup('<html><head><meta name="author" content="Jane Smith"></head></html>')
    assert ingest.extract_author(s) == "Jane Smith"


def test_extract_author_from_json_ld_object():
    schema = '{"@type":"Article","author":{"@type":"Person","name":"Ada Lovelace"}}'
    s = _soup(f'<html><head><script type="application/ld+json">{schema}</script></head></html>')
    assert ingest.extract_author(s, schema) == "Ada Lovelace"


def test_extract_author_from_json_ld_bare_string():
    schema = '{"@type":"Article","author":"Grace Hopper"}'
    assert ingest.extract_author(_soup("<html></html>"), schema) == "Grace Hopper"


def test_extract_author_from_json_ld_list_takes_first():
    schema = '{"@type":"Article","author":[{"name":"First Writer"},{"name":"Second Writer"}]}'
    assert ingest.extract_author(_soup("<html></html>"), schema) == "First Writer"


def test_extract_author_from_json_ld_graph():
    schema = '{"@graph":[{"@type":"WebSite"},{"@type":"Article","author":{"name":"Alan Turing"}}]}'
    assert ingest.extract_author(_soup("<html></html>"), schema) == "Alan Turing"


def test_extract_author_meta_wins_over_schema():
    schema = '{"@type":"Article","author":"Schema Name"}'
    s = _soup('<html><head><meta name="author" content="Meta Name"></head></html>')
    assert ingest.extract_author(s, schema) == "Meta Name"


def test_extract_author_skips_profile_urls():
    """article:author is often a Facebook profile URL, which is not a name."""
    s = _soup('<html><head><meta property="article:author" content="https://facebook.com/someone"></head></html>')
    assert ingest.extract_author(s) is None


def test_extract_author_none_when_page_declares_nothing():
    s = _soup("<html><body><p>An article written by hand, sorted by date.</p></body></html>")
    assert ingest.extract_author(s) is None


def test_extract_author_survives_malformed_json_ld():
    assert ingest.extract_author(_soup("<html></html>"), "{not valid json") is None


def test_extract_author_reads_rel_author_link():
    s = _soup('<html><body><a rel="author" href="/staff/kim">Kim Rivera</a></body></html>')
    assert ingest.extract_author(s) == "Kim Rivera"


def test_html_payload_includes_author_field():
    html = """<html><head><title>T</title><meta name="author" content="Sam Reed">
    </head><body><article><h1>Heading here</h1><p>Some body text for the article.</p></article></body></html>"""
    assert ingest.extract_html_payload(html, "https://example.com/a")["author"] == "Sam Reed"


def test_html_payload_author_is_none_when_absent():
    html = "<html><head><title>T</title></head><body><article><h1>Heading</h1><p>Body text here.</p></article></body></html>"
    assert ingest.extract_html_payload(html, "https://example.com/a")["author"] is None


def test_markdown_payload_has_null_author():
    assert ingest.extract_markdown_payload("# Title\n\nSome body text.\n", "a.md")["author"] is None
