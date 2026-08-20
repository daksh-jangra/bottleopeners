"""The Phase 1 payload contract, in one place.

Every phase consumes the same dict shape — that single decision is why the
rewrite round-trips through the analyzer unchanged. The definition used to be
copy-pasted into analyzer.py, generator.py, and rewriter.py, which put the
system's central contract at risk of drifting three ways.

Kept dependency-free (stdlib only) like common.py, so any phase can import it.

Each phase raises its own error type, and its CLI and the Flask app catch that
type by name, so the validator takes the exception class rather than defining
one here. Callers keep their error semantics; the checks live once.
"""

from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = {
    "source": str,
    "title": str,
    "meta_description": (str, type(None)),
    "headers": list,
    "body_text": str,
    "existing_schema": (str, type(None)),
    "published_date": (str, type(None)),
    "updated_date": (str, type(None)),
    "word_count": int,
}

# Fields that are allowed to be explicitly null.
NULLABLE_FIELDS = frozenset({
    "meta_description",
    "existing_schema",
    "published_date",
    "updated_date",
})


def validate_payload(payload: Any, error_cls: type[Exception]) -> dict[str, Any]:
    """Check the shared payload shape, raising error_cls on any violation.

    This is the contract every phase agrees on: the object exists, the required
    fields are present, and each holds the right type. Phase-specific checks
    (analyzer's header/date/count validation) layer on top of this.
    """
    if not isinstance(payload, dict):
        raise error_cls("Input JSON must be an object.")

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise error_cls(f"Input JSON is missing required fields: {', '.join(missing)}")

    for field, expected_type in REQUIRED_FIELDS.items():
        value = payload[field]
        if not isinstance(value, expected_type):
            if field in NULLABLE_FIELDS and value is None:
                continue
            type_name = expected_type if isinstance(expected_type, tuple) else expected_type.__name__
            raise error_cls(f"Field '{field}' has the wrong type; expected {type_name}.")

    return payload
