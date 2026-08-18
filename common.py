"""Small shared helpers used across the pipeline phases.

Kept dependency-free (stdlib only) so any phase can import it without pulling in
another phase's requirements.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Default Claude model used by the model-backed phases (rewrite, harness) and
# the dashboard. One place to change when swapping models.
DEFAULT_MODEL = "claude-opus-4-8"

# Cheaper, faster model for simple classification (e.g. sentiment labelling),
# where a small model is plenty. The answer-generation step still uses the
# default model so it stays representative of a real answer engine.
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"

# Google's answer engine, used by the citation harness alongside Claude. It is
# grounded in Google Search, so it surfaces a different source set than Claude's
# web search - which is the whole point of testing both.
#
# Pinned rather than "gemini-flash-latest": Search grounding bills against its
# own quota, and the -latest alias points at a newer model whose grounding quota
# is separate (and zero on some plans), so the alias 429s while this works.
GEMINI_MODEL = "gemini-2.5-flash"


def slugify(value: str) -> str:
    """Turn arbitrary text into a filesystem-safe slug (or 'output' if empty)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "output"


def mentions(haystack_lower: str, keyword: str) -> bool:
    """Whole-word/phrase match, so 'coffee' never trips the 'fee' keyword.

    Both the intent matcher and the hedge scorer match short words against
    free text, where a plain substring test produces false positives
    ('may' in 'Mayo', 'today' in 'todays'). Caller lowercases first.
    """
    return re.search(rf"\b{re.escape(keyword)}\b", haystack_lower) is not None


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env; real environment values win."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
