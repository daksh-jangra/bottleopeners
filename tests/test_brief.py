"""Tests for the content brief generator in brief.py.

build_brief is pure: a target question in, a deterministic brief out. These pin
the intent detection per question shape, the schema-type mapping, and the
guarantee that every brief is a complete plan against the same six rubric
factors Teardown grades - answer-first always leading.
"""

import brief
from rubric import FACTOR_LABELS


# --- intent detection -------------------------------------------------------

def test_how_to_intent():
    b = brief.build_brief("How to brew pour-over coffee")
    assert b["intent"] == "how_to"
    assert b["schema_type"] == "HowTo"


def test_comparison_intent():
    b = brief.build_brief("Chemex vs V60 for beginners")
    assert b["intent"] == "comparison"


def test_pricing_intent():
    b = brief.build_brief("How much does an espresso machine cost")
    # "how much" / "cost" is pricing, not how-to
    assert b["intent"] == "pricing"


def test_best_intent():
    b = brief.build_brief("Best burr grinder under 200")
    assert b["intent"] == "best"


def test_definition_intent():
    b = brief.build_brief("What is a flat white")
    assert b["intent"] == "definition"


def test_plain_question_falls_back():
    b = brief.build_brief("Does dark roast have more caffeine")
    assert b["intent"] == "question"
    assert b["schema_type"] == "FAQPage"


# --- title & outline --------------------------------------------------------

def test_title_reflects_intent_and_drops_question_mark():
    b = brief.build_brief("how to descale a kettle?")
    assert b["suggested_title"].startswith("How to descale a kettle")
    assert b["suggested_title"].endswith(": A Step-by-Step Guide")
    assert "?" not in b["suggested_title"]


def test_outline_leads_with_answer_first():
    b = brief.build_brief("Chemex vs V60")
    assert b["outline"][0] == "Answer-first opening"
    # intent sections follow
    assert "Side-by-side comparison table" in b["outline"]


# --- must-include maps the six factors --------------------------------------

def test_must_include_covers_all_six_factors_answer_first_leading():
    b = brief.build_brief("How to froth milk")
    keys = [m["factor"] for m in b["must_include"]]
    assert keys[0] == "answer_first_structure"          # always leads
    assert set(keys) == set(FACTOR_LABELS)              # all six present
    assert len(keys) == len(FACTOR_LABELS)              # no duplicates


def test_emphasis_factors_come_before_the_rest():
    b = brief.build_brief("How to froth milk")           # how_to emphasises lists/tables
    keys = [m["factor"] for m in b["must_include"]]
    assert keys.index("list_table_presence") < keys.index("recency_signals")


def test_every_must_include_has_a_why():
    b = brief.build_brief("What is cold brew")
    assert all(m["why"] for m in b["must_include"])


# --- guards -----------------------------------------------------------------

def test_empty_question_raises():
    import pytest
    with pytest.raises(ValueError):
        brief.build_brief("   ")


def test_brief_is_deterministic():
    a = brief.build_brief("Best pour-over kettle")
    b = brief.build_brief("Best pour-over kettle")
    assert a == b
