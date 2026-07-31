"""Tests for sitemap discovery and parsing in sitemap.py.

Everything here is pure - no network. These lock down turning a domain into the
sitemap URLs worth trying, reading both a <urlset> and a <sitemapindex>,
lifting Sitemap: lines out of robots.txt, and staying quiet on junk input.
"""

import sitemap

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
  <url><loc>https://example.com/pricing</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://example.com/blog/post</loc></url>
</urlset>"""

SITEMAPINDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
</sitemapindex>"""


# --- candidate_sitemap_urls / origin ----------------------------------------

def test_bare_domain_yields_conventional_locations():
    assert sitemap.candidate_sitemap_urls("example.com") == [
        "https://example.com/sitemap.xml",
        "https://example.com/sitemap_index.xml",
    ]


def test_full_xml_url_is_used_as_is():
    assert sitemap.candidate_sitemap_urls("https://x.com/custom/sitemap.xml") == [
        "https://x.com/custom/sitemap.xml",
    ]


def test_url_with_path_derives_origin():
    assert sitemap.candidate_sitemap_urls("https://example.com/blog/") == [
        "https://example.com/sitemap.xml",
        "https://example.com/sitemap_index.xml",
    ]


def test_empty_target_yields_nothing():
    assert sitemap.candidate_sitemap_urls("   ") == []
    assert sitemap.origin_of("") == ""


# --- parse_sitemap ----------------------------------------------------------

def test_parse_urlset_extracts_page_urls():
    out = sitemap.parse_sitemap(URLSET)
    assert out["urls"] == [
        "https://example.com/",
        "https://example.com/pricing",
        "https://example.com/blog/post",
    ]
    assert out["sitemaps"] == []


def test_parse_sitemapindex_extracts_nested_sitemaps():
    out = sitemap.parse_sitemap(SITEMAPINDEX)
    assert out["sitemaps"] == [
        "https://example.com/sitemap-pages.xml",
        "https://example.com/sitemap-posts.xml",
    ]
    assert out["urls"] == []


def test_malformed_xml_is_empty_not_raising():
    assert sitemap.parse_sitemap("<not xml") == {"urls": [], "sitemaps": []}
    assert sitemap.parse_sitemap("") == {"urls": [], "sitemaps": []}


def test_parser_tolerates_no_namespace():
    xml = "<urlset><url><loc>https://x.com/a</loc></url></urlset>"
    assert sitemap.parse_sitemap(xml)["urls"] == ["https://x.com/a"]


# --- sitemaps_from_robots ---------------------------------------------------

def test_robots_sitemap_directives_extracted():
    robots = (
        "User-agent: *\n"
        "Disallow: /admin\n"
        "Sitemap: https://example.com/sitemap.xml\n"
        "sitemap:  https://example.com/news.xml\n"  # case-insensitive, extra space
    )
    assert sitemap.sitemaps_from_robots(robots) == [
        "https://example.com/sitemap.xml",
        "https://example.com/news.xml",
    ]


def test_robots_without_sitemap_is_empty():
    assert sitemap.sitemaps_from_robots("User-agent: *\nDisallow:\n") == []
