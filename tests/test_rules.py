"""Unit tests for the Phase 2 scorers in rules.py.

Each scorer returns {"score", "max", "issues", "notes"}; these lock down the
scoring behavior (bounds, thresholds, and the key penalties/credits) so a
change to the heuristics can't silently move scores.

`issues` holds problems only and `notes` holds praise/observations. Downstream
consumers read issues directly to answer "what is wrong with this factor", so
the separation is pinned per-scorer at the bottom of this file.
"""

from datetime import date, timedelta

import rules
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
    assert isinstance(section["notes"], list)


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


def test_answer_first_hedges_match_whole_words_only():
    """'may' must not fire inside 'Mayo', nor 'today' inside 'todays'."""
    p = payload(body_text="Mayo Clinic is a nonprofit medical center. Todays hours are listed below.")
    section = score_answer_first_structure(p)
    assert not any("hedging" in i.lower() for i in section["issues"])


def test_answer_first_still_catches_a_real_hedge_word():
    p = payload(body_text="Coffee may be a brewed drink. It is prepared from beans.")
    section = score_answer_first_structure(p)
    assert any("hedging" in i.lower() for i in section["issues"])


def test_answer_first_still_catches_a_multiword_hedge_phrase():
    p = payload(body_text="In this article we explore coffee. It is a brewed drink.")
    section = score_answer_first_structure(p)
    assert any("hedging" in i.lower() for i in section["issues"])


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
        body_text="Coffee is a brewed drink enjoyed worldwide.",
        author="Jane Smith",
    )
    section = score_byline_authority(p)
    within_bounds(section)
    assert section["score"] >= 10
    # Positives land in notes; issues is problems only.
    assert any("Jane Smith" in n for n in section["notes"])


def test_byline_authority_zero_when_no_signals():
    assert score_byline_authority(payload(title="x"))["score"] == 0


def test_byline_authority_ignores_the_word_by_in_prose():
    """The old detector scored any page whose text contained ' by '."""
    p = payload(
        title="Results sorted by date",
        body_text="The entries below are sorted by date and were compiled by hand.",
    )
    section = score_byline_authority(p)
    assert any("No author is declared" in i for i in section["issues"])
    assert section["notes"] == [] or all("author" not in n.lower() for n in section["notes"])


def test_byline_authority_absent_author_field_degrades_to_no_author():
    """Payloads ingested before the author field existed must still score."""
    p = payload(title="Coffee Brewing Guide")
    assert "author" not in p
    section = score_byline_authority(p)
    within_bounds(section)
    assert any("No author is declared" in i for i in section["issues"])


def test_byline_authority_blank_author_is_not_an_author():
    section = score_byline_authority(payload(title="x", author="   "))
    assert any("No author is declared" in i for i in section["issues"])


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


# --- issues/notes separation ------------------------------------------------
#
# audit.py and teardown.py read `issues` directly to answer "what is wrong with
# this factor". That only works if praise never lands there. These pin the
# separation on the scorers that actually emit positive strings.

def test_list_table_credit_goes_to_notes_not_issues():
    p = payload(body_text="Intro.", list_count=2, table_count=1)
    section = score_list_table_presence(p)
    assert section["issues"] == []
    assert any("Table structure was found" in n for n in section["notes"])
    assert any("strong, citable structure" in n for n in section["notes"])


def test_list_table_absence_is_an_issue_not_a_note():
    section = score_list_table_presence(payload(body_text="A plain paragraph."))
    assert any("No strong list or table patterns" in i for i in section["issues"])


def test_recency_updated_date_praise_goes_to_notes():
    section = score_recency_signals(payload(
        published_date=(date.today() - timedelta(days=10)).isoformat(),
        updated_date=date.today().isoformat(),
    ))
    assert any("improves freshness" in n for n in section["notes"])
    assert not any("improves freshness" in i for i in section["issues"])


def test_recency_contradictory_dates_stay_an_issue():
    """A backwards updated date is a real problem and must remain in issues."""
    section = score_recency_signals(payload(
        published_date=date.today().isoformat(),
        updated_date=(date.today() - timedelta(days=30)).isoformat(),
    ))
    assert any("earlier than the published date" in i for i in section["issues"])


def test_factual_specificity_praise_goes_to_notes():
    body = "In 2021 Acme Corp shipped 4000 units to New York and Los Angeles on March 3."
    section = score_factual_specificity(payload(body_text=body, word_count=len(body.split())))
    assert any("easier to cite" in n for n in section["notes"])
    assert not any("easier to cite" in i for i in section["issues"])


def test_every_scorer_defaults_notes_to_a_list():
    """Scorers that never emit praise still return notes, so consumers can read it blind."""
    p = payload(headers=[{"level": 1, "text": "How to brew coffee properly"}], body_text="Coffee is a drink.")
    for section in (score_header_quality(p), score_answer_first_structure(p)):
        assert section["notes"] == []


# --- named entity detection -------------------------------------------------
#
# Feeds factual specificity via a density threshold, so a false entity inflates
# the score directly. Sentence-initial title case is the ambiguous case.

def test_entities_ignores_sentence_initial_title_case():
    """'Every Monday' is ordinary prose, not an organization."""
    assert rules._detect_named_entity_phrases("Every Monday the team meets.") == []


def test_entities_ignores_title_case_after_a_full_stop():
    text = "The process is simple. Another Plain Thing happens next."
    assert rules._detect_named_entity_phrases(text) == []


def test_entities_keeps_mid_sentence_title_case():
    text = "In 2021 Acme Corp shipped units to New York."
    found = rules._detect_named_entity_phrases(text)
    assert "Acme Corp" in found
    assert "New York" in found


def test_entities_detects_internal_capital_brands():
    found = rules._detect_named_entity_phrases("The iPhone and eBay both launched.")
    assert "iPhone" in found
    assert "eBay" in found


def test_entities_detects_acronyms_anywhere():
    """Acronyms are unambiguous, so position does not matter."""
    found = rules._detect_named_entity_phrases("NASA and the WHO agreed. It affects GDPR.")
    assert "NASA" in found
    assert "WHO" in found
    assert "GDPR" in found


def test_entities_deduplicates():
    found = rules._detect_named_entity_phrases("We use iPhone. Later the iPhone shipped.")
    assert found.count("iPhone") == 1


def test_entities_empty_for_plain_lowercase_prose():
    assert rules._detect_named_entity_phrases("this is a plain sentence with no entities") == []


def test_factual_specificity_not_inflated_by_capitalized_prose():
    """The false-positive path: title-case prose must not buy a specificity score."""
    body = ("Every Monday the team meets. The Best Way forward is clear. "
            "Another Good Reason exists. Some Other Thing matters too.")
    section = score_factual_specificity(payload(body_text=body, word_count=len(body.split())))
    assert not any("named entities" in n.lower() for n in section["notes"])
    assert any("named entities" in i.lower() for i in section["issues"])
