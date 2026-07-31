"""Auto-apply the one fix we can apply mechanically: the Schema.org markup.

Adding JSON-LD is the single citation-readiness fix that can be applied to a
page safely and deterministically, so this module strips any existing JSON-LD
and injects the generated block into the page's <head>, returning a patched
copy the user can publish as-is. The content fixes (headings, answer-first
structure) stay a punch list - rewriting those into arbitrary third-party
markup can't be done without risking the page, so we hand them back as a
checklist instead of guessing.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

# Matches a whole <script type="application/ld+json">...</script> block.
LDJSON_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>.*?</script>',
    re.IGNORECASE | re.DOTALL,
)


def strip_existing_ldjson(html: str) -> tuple[str, int]:
    """Drop existing JSON-LD blocks so the patch leaves one canonical block.

    The generated schema already merges whatever the page had, so removing the
    originals here avoids emitting duplicate, conflicting structured data.
    """
    cleaned, count = LDJSON_RE.subn("", html)
    return cleaned, count


def inject_schema(html: str, schema_script: str) -> dict[str, Any]:
    """Return a patched copy of `html` with `schema_script` added to the page.

    Inserts just before </head> (preferred), else before </body>, else appends.
    Existing JSON-LD is removed first. The rest of the page is left byte-for-byte
    unchanged, so the patch is a minimal, publishable diff.
    """
    cleaned, replaced = strip_existing_ldjson(html)
    block = "  " + schema_script.strip() + "\n"
    lowered = cleaned.lower()  # same length as cleaned, so indexes line up

    for anchor, location in (("</head>", "head"), ("</body>", "body")):
        idx = lowered.rfind(anchor)
        if idx != -1:
            patched = cleaned[:idx] + block + cleaned[idx:]
            return {"patched_html": patched, "location": location, "replaced_existing": replaced}

    return {"patched_html": cleaned + "\n" + block, "location": "appended", "replaced_existing": replaced}


# --- Standalone page export -------------------------------------------------
#
# The publish kit patches schema into a third-party page. This builds the other
# half: a clean, self-contained page from a *rewrite* we produced, so the user
# can ship the optimized content directly. It is deliberately paranoid - every
# piece of model-authored text is HTML-escaped, so nothing in the content can
# inject markup or script. The output is one static HTML document with a little
# inline CSS and no external assets.

PAGE_CSS = (
    "*{box-sizing:border-box}"
    "body{max-width:720px;margin:0 auto;padding:2.5rem 1.25rem;"
    "font:17px/1.65 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a}"
    "h1{font-size:2rem;line-height:1.2;margin:0 0 .5rem}"
    "h2{font-size:1.35rem;margin:2rem 0 .5rem}h3{font-size:1.1rem;margin:1.25rem 0 .25rem}"
    ".meta{color:#666;font-size:.9rem;margin:0 0 1.5rem}"
    ".lead{font-size:1.15rem;font-weight:600;margin:0 0 1.25rem}"
    "ul{padding-left:1.25rem}li{margin:.25rem 0}"
    "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.meta{color:#9aa}}"
)


def _paragraphs(body_text: str) -> list[str]:
    """Split prose into paragraphs on blank lines, collapsing inner whitespace."""
    chunks = re.split(r"\n\s*\n", (body_text or "").strip())
    return [re.sub(r"\s+", " ", c).strip() for c in chunks if c.strip()]


def _date_line(published_date: Optional[str], updated_date: Optional[str]) -> str:
    parts = []
    if published_date:
        parts.append(f"Published {html.escape(str(published_date))}")
    if updated_date and updated_date != published_date:
        parts.append(f"Updated {html.escape(str(updated_date))}")
    return f'<p class="meta">{" · ".join(parts)}</p>' if parts else ""


def build_standalone_page(
    *,
    title: str,
    body_text: str,
    meta_description: Optional[str] = None,
    answer_summary: Optional[str] = None,
    key_facts: Optional[list[str]] = None,
    faq: Optional[list[dict[str, str]]] = None,
    published_date: Optional[str] = None,
    updated_date: Optional[str] = None,
    schema_script: Optional[str] = None,
) -> str:
    """Render rewrite content into a clean, safe, standalone HTML page.

    All caller-supplied text is HTML-escaped; only `schema_script` (our own
    generated JSON-LD) is inserted verbatim, and its one dangerous sequence - a
    literal ``</`` that could close the <script> early - is neutralized.
    """
    esc_title = html.escape(str(title or "Untitled"))

    head = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc_title}</title>",
    ]
    if meta_description:
        head.append(f'<meta name="description" content="{html.escape(str(meta_description))}">')
    if schema_script:
        # Prevent a stray </script> inside the JSON from closing the tag early.
        head.append(schema_script.strip().replace("</", "<\\/"))
    head.append(f"<style>{PAGE_CSS}</style>")
    head.append("</head>")

    body = ["<body>", "<article>", f"<h1>{esc_title}</h1>"]
    date_line = _date_line(published_date, updated_date)
    if date_line:
        body.append(date_line)
    if answer_summary:
        body.append(f'<p class="lead">{html.escape(str(answer_summary))}</p>')
    for para in _paragraphs(body_text):
        body.append(f"<p>{html.escape(para)}</p>")

    facts = [f for f in (key_facts or []) if str(f).strip()]
    if facts:
        body.append("<h2>Key facts</h2>")
        body.append("<ul>" + "".join(f"<li>{html.escape(str(f))}</li>" for f in facts) + "</ul>")

    pairs = [p for p in (faq or []) if isinstance(p, dict) and p.get("question") and p.get("answer")]
    if pairs:
        body.append("<h2>Frequently asked questions</h2>")
        for pair in pairs:
            body.append(f"<h3>{html.escape(str(pair['question']))}</h3>")
            body.append(f"<p>{html.escape(str(pair['answer']))}</p>")

    body += ["</article>", "</body>", "</html>"]
    return "\n".join(head + body) + "\n"
