"""Tests for the Phase 3 schema generator in generator.py.

Covers the schema-type detection (FAQ vs HowTo vs Article), the question/step
header classifiers that drive it, section splitting, existing-schema parsing and
merging, and the end-to-end generate_schema() output for each type.
"""

import pytest

import generator


def payload(**over):
    base = {
        "source": "https://example.com/a",
        "title": "Sample Article",
        "meta_description": None,
        "headers": [],
        "body_text": "",
        "existing_schema": None,
        "published_date": None,
        "updated_date": None,
        "word_count": 0,
    }
    base.update(over)
    return base


def headers(*texts):
    return [{"level": 2, "text": t} for t in texts]


# --- validate_payload -------------------------------------------------------

def test_validate_payload_rejects_non_dict():
    with pytest.raises(generator.GeneratorError):
        generator.validate_payload(["not", "a", "dict"])


def test_validate_payload_reports_missing_fields():
    with pytest.raises(generator.GeneratorError, match="missing required fields"):
        generator.validate_payload({"title": "x"})


def test_validate_payload_allows_none_for_optional_fields():
    # meta_description/published_date/etc. may be None
    assert generator.validate_payload(payload()) is not None


def test_validate_payload_rejects_wrong_type():
    with pytest.raises(generator.GeneratorError, match="wrong type"):
        generator.validate_payload(payload(word_count="lots"))


# --- question / step header classifiers -------------------------------------

def test_is_question_header_variants():
    assert generator.is_question_header("How do I brew coffee?")
    assert generator.is_question_header("What is descaling")  # question word, no '?'
    assert generator.is_question_header("Q: Why bother")
    assert not generator.is_question_header("Brewing Overview")
    assert not generator.is_question_header("")


def test_is_step_header_variants():
    assert generator.is_step_header("Step 1: Grind the beans")
    assert generator.is_step_header("1. Grind the beans")
    assert generator.is_step_header("Step one", position=1)
    assert not generator.is_step_header("Grind the beans")
    assert not generator.is_step_header("")


# --- detect_type ------------------------------------------------------------

def test_detect_type_override_wins():
    assert generator.detect_type(payload(headers=headers("Anything")), "faq") == "faq"


def test_detect_type_faq_when_two_questions():
    p = payload(headers=headers("How do I brew?", "What beans are best?", "Storage"))
    assert generator.detect_type(p, None) == "faq"


def test_detect_type_howto_when_two_steps():
    p = payload(headers=headers("Step 1: Grind", "Step 2: Brew", "Step 3: Serve"))
    assert generator.detect_type(p, None) == "howto"


def test_detect_type_defaults_to_article():
    p = payload(headers=headers("Background", "Details", "Summary"))
    assert generator.detect_type(p, None) == "article"


def test_detect_type_faq_beats_howto_when_both_present():
    # questions checked first: two questions + two steps -> faq
    p = payload(headers=headers("How do I brew?", "What beans?", "Step 1: Grind", "Step 2: Brew"))
    assert generator.detect_type(p, None) == "faq"


# --- find_sections ----------------------------------------------------------

def test_find_sections_splits_body_by_headers():
    body = "How do I brew? Use a 1:2 ratio. What beans? Any fresh roast works."
    p = payload(headers=headers("How do I brew?", "What beans?"), body_text=body)
    sections = generator.find_sections(p)
    assert [s["header"] for s in sections] == ["How do I brew?", "What beans?"]
    assert "1:2 ratio" in sections[0]["body"]
    assert "fresh roast" in sections[1]["body"]


# --- existing schema parsing / merge ----------------------------------------

def test_parse_existing_schema_single_object():
    assert generator.parse_existing_schema('{"@type":"Article"}') == {"@type": "Article"}


def test_parse_existing_schema_none_and_garbage():
    assert generator.parse_existing_schema(None) is None
    assert generator.parse_existing_schema("   ") is None
    assert generator.parse_existing_schema("not json") is None


def test_parse_existing_schema_multiple_blocks_returns_list():
    raw = '{"@type":"Article"}\n\n{"@type":"WebPage"}'
    parsed = generator.parse_existing_schema(raw)
    assert isinstance(parsed, list)
    assert {"@type": "Article"} in parsed and {"@type": "WebPage"} in parsed


def test_deep_merge_prefers_base_and_adds_overlay_keys():
    base = {"@type": "Article", "headline": "Real"}
    overlay = {"headline": "Generated", "wordCount": 100}
    merged = generator.deep_merge(base, overlay)
    assert merged["headline"] == "Real"  # existing value preserved
    assert merged["wordCount"] == 100    # new key added


# --- generate_schema end to end ---------------------------------------------

def test_generate_schema_article_notes_missing_fields():
    p = payload(headers=headers("Background"), body_text="Coffee is a brewed drink.")
    generated, notes, merge = generator.generate_schema(p, "article")
    assert generated["@type"] == "Article"
    assert merge is None
    assert any("meta description" in n for n in notes)
    assert any("published date" in n for n in notes)


def test_generate_schema_faq_builds_main_entity():
    body = "How do I brew? Use a 1:2 ratio. What beans? Any fresh roast."
    p = payload(headers=headers("How do I brew?", "What beans?"), body_text=body)
    generated, _notes, _merge = generator.generate_schema(p, "faq")
    assert generated["@type"] == "FAQPage"
    names = [q["name"] for q in generated["mainEntity"]]
    assert "How do I brew?" in names


def test_generate_schema_howto_builds_steps():
    body = "Step 1: Grind the beans finely. Step 2: Brew for four minutes."
    p = payload(headers=headers("Step 1: Grind", "Step 2: Brew"), body_text=body)
    generated, _notes, _merge = generator.generate_schema(p, "howto")
    assert generated["@type"] == "HowTo"
    assert len(generated["step"]) == 2


def test_generate_schema_merges_existing_schema():
    p = payload(
        headers=headers("Background"),
        body_text="Coffee is a brewed drink.",
        existing_schema='{"@type":"Article","author":{"@type":"Person","name":"Jane"}}',
    )
    generated, notes, merge = generator.generate_schema(p, "article")
    assert merge is not None
    assert merge["author"]["name"] == "Jane"  # existing detail carried through
    assert any("existing schema found" in n for n in notes)
