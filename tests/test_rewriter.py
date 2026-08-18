"""Tests for the Phase 4 rewriter's payload merge.

Focuses on merge_rewrite(), the step that turns a model response back into a
Phase 1-shaped payload for re-scoring. The structure counts are the sensitive
part: they feed score_list_table_presence, which grades the very rewrite that
produced them, so they must be derived from the markup rather than reported.
"""

import rewriter


def source_payload(**overrides):
    base = {
        "source": "https://example.com/a",
        "title": "Original title",
        "meta_description": "Original meta.",
        "headers": [{"level": 1, "text": "Original"}],
        "body_text": "Original body.",
        "existing_schema": None,
        "published_date": "2025-01-01",
        "updated_date": None,
        "word_count": 2,
    }
    base.update(overrides)
    return base


# --- structure counts are measured, not self-reported -----------------------

def test_merge_ignores_inflated_self_reported_counts():
    """A model claiming lists it did not write must not score for them."""
    merged = rewriter.assemble_rewritten_payload(
        source_payload(),
        {"body_text": "Just a flat paragraph with no structure.",
         "headers": [], "list_count": 9, "table_count": 4},
    )
    assert merged["list_count"] == 0
    assert merged["table_count"] == 0


def test_merge_counts_real_markdown_structure():
    body = "Intro line.\n\n- one\n- two\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"
    merged = rewriter.assemble_rewritten_payload(
        source_payload(), {"body_text": body, "headers": []},
    )
    assert merged["list_count"] == 1
    assert merged["table_count"] == 1


def test_merge_credits_structure_the_model_did_not_claim():
    """The count is honest in both directions, not just downward."""
    merged = rewriter.assemble_rewritten_payload(
        source_payload(),
        {"body_text": "- a\n- b\n", "headers": [], "list_count": 0, "table_count": 0},
    )
    assert merged["list_count"] == 1


# --- the rest of the merge --------------------------------------------------

def test_merge_falls_back_to_source_title_and_meta():
    merged = rewriter.assemble_rewritten_payload(source_payload(), {"body_text": "x", "headers": []})
    assert merged["title"] == "Original title"
    assert merged["meta_description"] == "Original meta."


def test_merge_carries_source_only_fields_through():
    merged = rewriter.assemble_rewritten_payload(
        source_payload(existing_schema='{"@type":"Article"}'),
        {"body_text": "x", "headers": [], "title": "New title"},
    )
    assert merged["title"] == "New title"
    assert merged["source"] == "https://example.com/a"
    assert merged["existing_schema"] == '{"@type":"Article"}'
    assert merged["published_date"] == "2025-01-01"


def test_merge_drops_malformed_headers():
    merged = rewriter.assemble_rewritten_payload(
        source_payload(),
        {"body_text": "x", "headers": [{"level": 2, "text": "Kept"}, {"text": "no level"}, "junk"]},
    )
    assert merged["headers"] == [{"level": 2, "text": "Kept"}]


def test_merge_recomputes_word_count_from_rewritten_body():
    merged = rewriter.assemble_rewritten_payload(
        source_payload(), {"body_text": "one two three four", "headers": []},
    )
    assert merged["word_count"] == 4


# --- the merged payload must survive the analyzer ---------------------------

def test_merged_payload_passes_analyzer_validation():
    """The round-trip is the whole premise: Phase 2 runs on Phase 4 output."""
    import analyzer

    merged = rewriter.assemble_rewritten_payload(
        source_payload(),
        {"body_text": "- a\n- b\n", "headers": [{"level": 1, "text": "H"}]},
    )
    assert analyzer.validate_payload(merged) is not None
