"""Sitemap discovery and parsing for site-wide batch analyze.

Pure XML/URL handling, kept apart from the network so it unit-tests without a
single fetch. The app layer pulls bytes (through ingest's SSRF-guarded fetcher)
and hands them here: this module turns a domain into the sitemap URLs worth
trying, reads a <urlset> into page URLs, reads a <sitemapindex> into nested
sitemap URLs (followed one level by the caller), and lifts `Sitemap:` lines out
of robots.txt. Everything is stdlib - no new dependency.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlparse


def ensure_scheme(target: str) -> str:
    """Prefix https:// when a bare domain is given, so urlparse sees a host."""
    target = target.strip()
    if not target:
        return ""
    if "://" in target:
        return target
    return "https://" + target


def origin_of(target: str) -> str:
    """The scheme://host origin for a domain or URL, or '' if unparseable."""
    parsed = urlparse(ensure_scheme(target))
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def candidate_sitemap_urls(target: str) -> list[str]:
    """Sitemap URLs to try for a target, in order.

    A target that already points at an .xml is used as-is; otherwise fall back
    to the two conventional locations at the origin.
    """
    target = target.strip()
    if not target:
        return []
    if target.lower().rstrip("/").endswith(".xml"):
        return [ensure_scheme(target)]
    origin = origin_of(target)
    if not origin:
        return []
    return [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]


def _localname(tag: str) -> str:
    """Strip any XML namespace so {ns}loc and loc compare equal."""
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml: str) -> dict[str, list[str]]:
    """Parse sitemap XML into page URLs and nested sitemap URLs.

    Returns {"urls": [...], "sitemaps": [...]}. A <urlset> yields page urls; a
    <sitemapindex> yields nested sitemaps. Malformed XML yields empty lists
    rather than raising - a bad sitemap shouldn't crash a batch run.
    """
    urls: list[str] = []
    sitemaps: list[str] = []
    try:
        root = ET.fromstring((xml or "").strip())
    except ET.ParseError:
        return {"urls": urls, "sitemaps": sitemaps}

    for entry in root:
        name = _localname(entry.tag)
        loc = None
        for child in entry:
            if _localname(child.tag) == "loc" and child.text and child.text.strip():
                loc = child.text.strip()
                break
        if not loc:
            continue
        if name == "sitemap":
            sitemaps.append(loc)
        elif name == "url":
            urls.append(loc)
    return {"urls": urls, "sitemaps": sitemaps}


def sitemaps_from_robots(robots_txt: str) -> list[str]:
    """Extract the URLs from `Sitemap:` directives in a robots.txt body."""
    found: list[str] = []
    for line in (robots_txt or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "sitemap":
            url = value.strip()
            if url:
                found.append(url)
    return found
