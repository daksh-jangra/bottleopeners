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


# --- build_standalone_page: the "ship the fix" export ------------------------

def _page(**kw):
    base = dict(title="Pour-Over Coffee", body_text="First para.\n\nSecond para.")
    base.update(kw)
    return export.build_standalone_page(**base)


def test_page_is_a_full_html_document():
    page = _page()
    assert page.startswith("<!DOCTYPE html>")
    assert "<html lang=\"en\">" in page
    assert page.rstrip().endswith("</html>")
    assert "<h1>Pour-Over Coffee</h1>" in page


def test_body_text_becomes_paragraphs():
    page = _page(body_text="Alpha line.\n\nBeta line.\n\nGamma line.")
    assert page.count("<p>") >= 3
    assert "<p>Alpha line.</p>" in page
    assert "<p>Beta line.</p>" in page


def test_answer_summary_is_the_lead():
    page = _page(answer_summary="The direct answer up front.")
    assert '<p class="lead">The direct answer up front.</p>' in page


def test_key_facts_render_as_a_list():
    page = _page(key_facts=["96C water", "1:16 ratio"])
    assert "<h2>Key facts</h2>" in page
    assert "<li>96C water</li>" in page
    assert "<li>1:16 ratio</li>" in page


def test_faq_renders_questions_and_answers():
    page = _page(faq=[{"question": "How hot?", "answer": "About 96C."}])
    assert "<h2>Frequently asked questions</h2>" in page
    assert "<h3>How hot?</h3>" in page
    assert "<p>About 96C.</p>" in page


def test_dates_render_when_present_and_dedupe():
    page = _page(published_date="2026-01-01", updated_date="2026-03-01")
    assert "Published 2026-01-01" in page
    assert "Updated 2026-03-01" in page
    same = _page(published_date="2026-01-01", updated_date="2026-01-01")
    assert same.count("2026-01-01") == 1   # not shown twice when identical


def test_all_content_is_html_escaped():
    page = export.build_standalone_page(
        title="<script>alert(1)</script>",
        body_text="Body with <img src=x onerror=alert(2)> tag",
        answer_summary="<b>bold</b> lead",
        key_facts=["<i>fact</i>"],
        faq=[{"question": "<u>q</u>", "answer": "<em>a</em>"}],
    )
    # no raw injected tags survive from model content
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;img src=x" in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page


def test_schema_script_injected_and_defused():
    schema = '<script type="application/ld+json">{"@type":"Article","name":"x</script>evil"}</script>'
    page = _page(schema_script=schema)
    # the JSON-LD is present in the head...
    assert "application/ld+json" in page
    # ...but the dangerous closing sequence is neutralized so it can't break out
    assert "x</script>evil" not in page
    assert "<\\/" in page


def test_optional_sections_absent_when_empty():
    page = _page()   # no facts, no faq, no dates, no schema
    assert "Key facts" not in page
    assert "Frequently asked questions" not in page
    assert 'class="meta"' not in page
