"""Unit tests for the Phase 2 scorers in rules.py.

Each scorer returns {"score", "max", "issues"}; these lock down the scoring
behavior (bounds, thresholds, and the key penalties/credits) so a change to the
heuristics can't silently move scores.
"""

from datetime import date, timedelta

from rules import (
    ANSWER_FIRST_MAX_SCORE,
    BYLINE_AUTHORITY_MAX_SCORE,
    FACTUAL_SPECIFICITY_MAX_SCORE,
    HEADER_MAX_SCORE,
    LIST_TABLE_MAX_SCORE,
    RECENCY_MAX_SCORE,
    score_answer_first_structure,
    score_byline_authority,
    score_factual_specificity,
    score_header_quality,
    score_list_table_presence,
    score_recency_signals,
)


def payload(**over):
    base = {
        "title": "Untitled",
        "meta_description": None,
        "headers": [],
        "body_text": "",
        "existing_schema": None,
        "published_date": None,
        "updated_date": None,
        "word_count": 0,
        "list_count": 0,
        "table_count": 0,
    }
    base.update(over)
    return base


def within_bounds(section):
    assert 0 <= section["score"] <= section["max"]
    assert isinstance(section["issues"], list)


# --- header quality ---------------------------------------------------------

def test_header_quality_full_marks_for_clean_hierarchy():
    p = payload(headers=[
        {"level": 1, "text": "How to descale a coffee maker"},
        {"level": 2, "text": "What you need before you start"},
        {"level": 2, "text": "Step by step descaling guide"},
    ])
    section = score_header_quality(p)
    within_bounds(section)
    assert section["score"] == HEADER_MAX_SCORE


def test_header_quality_zero_when_no_headers():
    assert score_header_quality(payload(headers=[]))["score"] == 0


def test_header_quality_penalizes_vague_header():
    section = score_header_quality(payload(headers=[{"level": 1, "text": "Overview"}]))
    within_bounds(section)
    assert section["score"] < HEADER_MAX_SCORE
    assert any("vague" in i.lower() for i in section["issues"])


def test_header_quality_flags_missing_h1():
    section = score_header_quality(payload(headers=[
        {"level": 2, "text": "What you should know about coffee"},
        {"level": 2, "text": "How to brew a better cup"},
    ]))
    assert any("h1" in i.lower() for i in section["issues"])


# --- answer-first structure -------------------------------------------------

def test_answer_first_full_marks_for_direct_lead():
    p = payload(body_text="Coffee is a brewed drink prepared from roasted coffee beans.")
    section = score_answer_first_structure(p)
    within_bounds(section)
    assert section["score"] == ANSWER_FIRST_MAX_SCORE


def test_answer_first_penalizes_hedging_opener():
    p = payload(body_text="In this article we will explore whether coffee might help you focus.")
    section = score_answer_first_structure(p)
    within_bounds(section)
    assert section["score"] < ANSWER_FIRST_MAX_SCORE


def test_answer_first_zero_when_body_empty():
    assert score_answer_first_structure(payload(body_text=""))["score"] == 0


# --- lists & tables ---------------------------------------------------------

def test_list_table_full_marks_with_lists_and_table():
    p = payload(body_text="- one\n- two\n| a | b |", list_count=2, table_count=1)
    section = score_list_table_presence(p)
    within_bounds(section)
    assert section["score"] == LIST_TABLE_MAX_SCORE


def test_list_table_zero_with_no_structure():
    p = payload(body_text="Just a plain paragraph of prose with no structure at all.")
    assert score_list_table_presence(p)["score"] == 0


# --- factual specificity ----------------------------------------------------

def test_factual_specificity_high_when_dense():
    body = "In 2021 Acme Corp shipped 4000 units to New York and Los Angeles on March 3."
    section = score_factual_specificity(payload(body_text=body, word_count=len(body.split())))
    within_bounds(section)
    assert section["score"] >= 9


def test_factual_specificity_low_when_sparse():
    body = "this is a plain sentence without any facts here"
    section = score_factual_specificity(payload(body_text=body, word_count=len(body.split())))
    assert section["score"] <= 5


def test_factual_specificity_zero_when_no_words():
    assert score_factual_specificity(payload(body_text="", word_count=0))["score"] == 0


# --- byline authority -------------------------------------------------------

def test_byline_authority_credits_author_and_publisher():
    p = payload(
        title="Coffee Brewing Guide",
        meta_description="A detailed guide to brewing better coffee at home with practical ratios and timing.",
        existing_schema='{"@type":"Organization","name":"Acme Inc","publisher":"Acme"}',
        body_text="Written by Jane Smith. Coffee is a brewed drink enjoyed worldwide.",
    )
    section = score_byline_authority(p)
    within_bounds(section)
    assert section["score"] >= 10
    assert any("author" in i.lower() or "byline" in i.lower() for i in section["issues"])


def test_byline_authority_zero_when_no_signals():
    assert score_byline_authority(payload(title="x"))["score"] == 0


# --- recency ----------------------------------------------------------------

def test_recency_full_marks_for_today():
    section = score_recency_signals(payload(published_date=date.today().isoformat()))
    within_bounds(section)
    assert section["score"] == RECENCY_MAX_SCORE


def test_recency_decays_for_old_content():
    old = (date.today() - timedelta(days=365 * 8)).isoformat()
    section = score_recency_signals(payload(published_date=old))
    assert 0 <= section["score"] <= 2


def test_recency_zero_without_any_date():
    assert score_recency_signals(payload())["score"] == 0
