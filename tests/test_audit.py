"""Tests for the AEO audit in audit.py.

Covers the schema-type parser, the negative-issue picker, and the end-to-end
build_audit() shape: category grouping, the summary tallies, the technical
checks, and — most importantly — the fix routing that the "Fix this ->" buttons
depend on.
"""

import analyzer
import audit
import ingest

RICH_HTML = """<html><head>
<title>How do I descale a coffee maker?</title>
<meta name="description" content="Descaling a coffee maker takes about 30 minutes using a 1:1 mix of white vinegar and water to remove limescale.">
<meta property="og:title" content="Descale a coffee maker">
<meta property="og:description" content="Step-by-step descaling">
<meta property="og:image" content="https://example.com/x.png">
<meta property="og:url" content="https://example.com/descale">
<meta property="og:type" content="article">
<link rel="canonical" href="https://example.com/descale">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{"@type":"Question","name":"How often?"},{"@type":"Question","name":"What descaler?"}]}</script>
</head><body><main><h1>How do I descale a coffee maker?</h1>
<p>Descaling a coffee maker means running a 1:1 vinegar-water solution through it to remove limescale, last updated March 3, 2025.</p>
<h2>Steps</h2><ol><li>Mix vinegar and water</li><li>Run a cycle</li><li>Rinse twice</li></ol>
<table><tr><td>Vinegar</td><td>2 cups</td></tr></table></main></body></html>"""

POOR_HTML = "<html><head><title>Coffee</title></head><body><main><h1>Coffee</h1>" \
            "<p>In this article we will explore coffee.</p></main></body></html>"


def run_audit(html, url="https://example.com/descale", headers=None):
    payload = ingest.extract_html_payload(html, url)
    analysis = analyzer.build_analysis(payload)
    return audit.build_audit(payload, analysis, html, url, headers or {})


def by_check(result):
    return {c["check"]: c for cat in result["categories"] for c in cat["checks"]}


# --- helpers ----------------------------------------------------------------

def test_extract_schema_types_string_and_array():
    assert audit._extract_schema_types('{"@type":"Article"}') == ["Article"]
    assert audit._extract_schema_types('{"@type":["FAQPage","WebPage"]}') == ["FAQPage", "WebPage"]
    assert audit._extract_schema_types(None) == []


def test_negative_issue_prefers_a_problem():
    section = {"issues": [
        "Author or editorial byline cues were detected.",
        "No meta description was provided; add one.",
    ]}
    assert audit._negative_issue(section) == "No meta description was provided; add one."


def test_negative_issue_none_when_all_positive():
    section = {"issues": ["Author cues were detected.", "Table structure was found."]}
    assert audit._negative_issue(section) is None


# --- build_audit shape ------------------------------------------------------

def test_summary_tallies_add_up():
    result = run_audit(RICH_HTML, headers={"Strict-Transport-Security": "max-age=1"})
    s = result["summary"]
    assert s["pass"] + s["warn"] + s["fail"] == s["total"]
    n_checks = sum(len(cat["checks"]) for cat in result["categories"])
    assert s["total"] == n_checks == 14


def test_category_order():
    result = run_audit(RICH_HTML)
    names = [cat["name"] for cat in result["categories"]]
    assert names == ["Structure & Schema", "Content Quality", "Technical", "Server-Side Rendering"]


# --- technical checks -------------------------------------------------------

def test_https_and_hsts_pass_on_secure_page():
    checks = by_check(run_audit(RICH_HTML, url="https://example.com/descale",
                                headers={"Strict-Transport-Security": "max-age=1"}))
    assert checks["HTTPS"]["status"] == "pass"
    assert checks["HSTS"]["status"] == "pass"


def test_https_fails_on_http_url():
    checks = by_check(run_audit(RICH_HTML, url="http://example.com/descale"))
    assert checks["HTTPS"]["status"] == "fail"


def test_canonical_pass_and_no_fix():
    checks = by_check(run_audit(RICH_HTML))
    assert checks["Canonical tag"]["status"] == "pass"
    assert checks["Canonical tag"]["fix"] is None


# --- fix routing (what the "Fix this ->" buttons rely on) -------------------

def test_schema_missing_routes_to_schema_tab():
    checks = by_check(run_audit(POOR_HTML))
    assert checks["Schema.org markup"]["status"] == "fail"
    assert checks["Schema.org markup"]["fix"] == "schema"


def test_faq_present_is_detected():
    checks = by_check(run_audit(RICH_HTML))
    assert checks["FAQ schema"]["status"] == "pass"
    assert "2 question" in checks["FAQ schema"]["detail"]


def test_open_graph_is_advice_only():
    checks = by_check(run_audit(POOR_HTML))
    assert checks["Open Graph tags"]["fix"] is None


def test_meta_description_routes_to_rewrite():
    checks = by_check(run_audit(POOR_HTML))
    assert checks["Meta description"]["status"] == "fail"
    assert checks["Meta description"]["fix"] == "rewrite"


def test_freshness_is_advice_only_but_content_rows_route_to_rewrite():
    checks = by_check(run_audit(POOR_HTML))
    # freshness can't be auto-fixed (never invent a date)
    assert checks["Freshness / dates"]["fix"] is None
    # a different content factor still routes to rewrite
    assert checks["Heading hierarchy"]["fix"] == "rewrite"


def test_js_free_rendering_passes_with_real_content():
    long_body = "word " * 150
    html = f"<html><head><title>T</title></head><body><main><h1>T</h1><p>{long_body}</p></main></body></html>"
    checks = by_check(run_audit(html))
    assert checks["JS-free rendering"]["status"] == "pass"


def test_js_free_rendering_warns_on_thin_content():
    # RICH_HTML has readable-but-thin body text (well under 100 words)
    checks = by_check(run_audit(RICH_HTML))
    assert checks["JS-free rendering"]["status"] == "warn"
