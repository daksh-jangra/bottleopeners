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

import re
from typing import Any

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
