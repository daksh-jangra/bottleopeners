"""Tests for the Niche Explorer helpers in harness.py.

Covers the two pure functions behind query suggestion: clean_queries (trim,
de-dupe, cap) and build_niche_prompt (includes the provided context, and omits
the brand from the questions' intent). The Claude call itself is not unit-tested
(it needs the API); these lock down the parsing/prompting around it.
"""

import harness


# --- clean_queries ----------------------------------------------------------

def test_clean_queries_trims_and_drops_blanks():
    out = harness.clean_queries(["  how to open a bottle  ", "", "   ", "best bar tools"])
    assert out == ["how to open a bottle", "best bar tools"]


def test_clean_queries_dedupes_case_insensitively():
    out = harness.clean_queries(["Best Bottle Opener", "best bottle opener", "BEST bottle opener"])
    assert out == ["Best Bottle Opener"]


def test_clean_queries_collapses_internal_whitespace():
    assert harness.clean_queries(["how   do\tI  open\nthis"]) == ["how do I open this"]


def test_clean_queries_caps_to_limit():
    out = harness.clean_queries([f"question number {i}" for i in range(20)], limit=5)
    assert len(out) == 5


def test_clean_queries_ignores_non_strings():
    assert harness.clean_queries(["real question", 42, None, {"q": "x"}]) == ["real question"]


# --- build_niche_prompt -----------------------------------------------------

def test_build_niche_prompt_includes_all_context():
    prompt = harness.build_niche_prompt("acme.com", "bottle openers, bar tools", "kitchenware", 8)
    assert "acme.com" in prompt
    assert "bottle openers, bar tools" in prompt
    assert "kitchenware" in prompt
    assert "8" in prompt


def test_build_niche_prompt_omits_absent_optional_fields():
    prompt = harness.build_niche_prompt("acme.com", "", "", 6)
    assert "acme.com" in prompt
    # no industry / keywords labels when those inputs are empty
    assert "Industry / vertical:" not in prompt
    assert "Target keywords:" not in prompt


def test_build_niche_prompt_instructs_not_to_name_the_brand():
    prompt = harness.build_niche_prompt("acme.com", "bar tools", "", 8)
    assert "Do NOT mention the brand" in prompt
