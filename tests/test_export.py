"""Tests for the schema-injection auto-fix in export.py.

inject_schema() must place the generated JSON-LD in the right spot, remove any
existing JSON-LD so there's one canonical block, and leave the rest of the page
untouched. These lock down the anchors, the strip-and-replace, and the
byte-preserving fallback behavior.
"""

import export

SCHEMA = '<script type="application/ld+json">{"@type":"Article"}</script>'


def test_injects_before_head_close():
    html = "<html><head><title>T</title></head><body><p>hi</p></body></html>"
    result = export.inject_schema(html, SCHEMA)
    assert result["location"] == "head"
    # schema lands inside <head>, before the closing tag
    head = result["patched_html"].split("</head>")[0]
    assert SCHEMA in head
    assert result["replaced_existing"] == 0


def test_strips_existing_ldjson_and_replaces_it():
    old = '<script type="application/ld+json">{"@type":"WebPage"}</script>'
    html = f"<html><head>{old}<title>T</title></head><body>x</body></html>"
    result = export.inject_schema(html, SCHEMA)
    assert result["replaced_existing"] == 1
    assert '"WebPage"' not in result["patched_html"]      # old block gone
    assert SCHEMA in result["patched_html"]                # new one present
    assert result["patched_html"].count("application/ld+json") == 1


def test_falls_back_to_body_when_no_head():
    html = "<body><p>content</p></body>"
    result = export.inject_schema(html, SCHEMA)
    assert result["location"] == "body"
    assert SCHEMA in result["patched_html"].split("</body>")[0]


def test_appends_when_no_head_or_body():
    html = "<p>just a fragment</p>"
    result = export.inject_schema(html, SCHEMA)
    assert result["location"] == "appended"
    assert result["patched_html"].strip().endswith("</script>")


def test_head_match_is_case_insensitive():
    html = "<HTML><HEAD></HEAD><BODY></BODY></HTML>"
    result = export.inject_schema(html, SCHEMA)
    assert result["location"] == "head"
    assert SCHEMA in result["patched_html"].split("</HEAD>")[0]


def test_preserves_the_rest_of_the_page():
    html = "<html><head><title>Keep me</title></head><body><main>Body kept</main></body></html>"
    patched = export.inject_schema(html, SCHEMA)["patched_html"]
    assert "<title>Keep me</title>" in patched
    assert "<main>Body kept</main>" in patched


def test_strip_existing_counts_multiple_blocks():
    html = (
        '<script type="application/ld+json">{"a":1}</script>'
        '<script type="application/ld+json">{"b":2}</script>'
    )
    cleaned, count = export.strip_existing_ldjson(html)
    assert count == 2
    assert "ld+json" not in cleaned
