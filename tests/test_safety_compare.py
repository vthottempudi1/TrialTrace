##############################################################################
# tests/test_safety_compare.py
#
# PURPOSE: Prove the safety comparison rules (step S7): comparability is checked before any number
#          (definition, group, registry counterpart, population), equal counts out of different
#          populations are NOT "consistent", an uncertain group or event name is "unclear", missing
#          data is "unknown" and never "no difference", and no answer uses a verdict word.
##############################################################################

from trialtrace.safety.safety_compare import (
    compare_statement, compare_statements, event_spellings, find_event_rows, match_arm, registry_not_in_abstract,
)
from trialtrace.safety.safety_llm import ModelSafetyStatement
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction

NCT = "NCT04644237"
HASH = "a" * 64


def arm(position, title, deaths=None, serious=None, at_risk=None):
    return SafetyArm(
        nct_id=NCT, content_hash=HASH, position=position, group_id=f"EG00{position}", title=title,
        deaths_affected=deaths, deaths_at_risk=at_risk if deaths is not None else None,
        serious_affected=serious, serious_at_risk=at_risk if serious is not None else None,
    )


def event(kind, position, term, affected, at_risk, group_id="EG000"):
    return SafetyEvent(
        nct_id=NCT, content_hash=HASH, kind=kind, position=position, term=term,
        counts=[SafetyEventCount(group_id=group_id, affected=affected, at_risk=at_risk)],
    )


def registry(events=None, threshold=5.0):
    """Two groups: Drug X (6 deaths, 42 serious, of 101) and Placebo (4 deaths, 26 serious, of 50)."""
    arms = [arm(0, "Drug X", deaths=6, serious=42, at_risk=101), arm(1, "Placebo", deaths=4, serious=26, at_risk=50)]
    return SafetyExtraction(status="ok", frequency_threshold=threshold, arms=arms, events=events or [])


def said(kind="serious", arm="Drug X", arm_in_quote=True, **fields):
    return ModelSafetyStatement(kind=kind, arm=arm, arm_in_quote=arm_in_quote, **fields)


# ---- the numbers -------------------------------------------------------------

def test_same_count_and_same_population_is_consistent():
    result = compare_statement(said(count=42, denominator=101, percent=41.6), registry())
    assert result.category == "safety_consistent"
    assert (result.registry_arm, result.registry_affected, result.registry_at_risk) == ("Drug X", 42, 101)


def test_a_different_count_in_the_same_population_differs():
    result = compare_statement(said(count=40, denominator=101), registry())
    assert result.category == "safety_number_differs"
    assert "40 of 101" in result.reason and "42 of 101" in result.reason


def test_equal_counts_out_of_different_populations_are_not_consistent():
    # registry 42 of 101 treated, abstract 42 of 102 randomised (SAFETY_PLAN section 12)
    result = compare_statement(said(count=42, denominator=102), registry())
    assert result.category == "safety_not_comparable"
    assert "Different populations" in result.reason


def test_a_percent_is_compared_within_rounding():
    assert compare_statement(said(denominator=101, percent=42.0), registry()).category == "safety_consistent"       # 41.58%
    assert compare_statement(said(denominator=101, percent=45.0), registry()).category == "safety_number_differs"


def test_no_denominator_in_the_abstract_is_not_comparable():
    for fields in ({"count": 42}, {"percent": 41.6}, {"count": 42, "percent": 41.6}):
        result = compare_statement(said(**fields), registry())
        assert result.category == "safety_not_comparable"
        assert "42 of 101" in result.reason         # the reviewer still sees the registry number


def test_no_deaths_against_registry_deaths_differs():
    result = compare_statement(said(kind="death", count=0, denominator=101, negated=True), registry())
    assert result.category == "safety_number_differs"


def test_registry_without_at_risk_is_not_comparable():
    safety = registry()
    safety.arms[0].serious_at_risk = None
    assert compare_statement(said(count=42, denominator=101), safety).category == "safety_not_comparable"


# ---- the definition ----------------------------------------------------------

def test_a_grade_is_never_compared_with_the_registry():
    events = [event("other", 0, "Neutropenia", 21, 101)]
    result = compare_statement(said(kind="event", term="neutropenia", grade="grade 3 or higher", count=21, denominator=101), registry(events))
    assert result.category == "safety_not_comparable"
    assert "grade" in result.reason


def test_treatment_related_and_one_cause_are_not_all_deaths():
    related = compare_statement(said(kind="death", count=6, denominator=101, treatment_related=True), registry())
    cause = compare_statement(said(kind="death", count=6, denominator=101, cause="cardiovascular"), registry())
    assert related.category == "safety_not_comparable"
    assert cause.category == "safety_not_comparable"


# ---- the group ---------------------------------------------------------------

def test_arm_matching_by_title():
    arms = registry().arms
    assert match_arm("the placebo group", arms).title == "Placebo"
    assert match_arm("drug x", arms).title == "Drug X"
    assert match_arm("trastuzumab", arms) is None           # another name: not certain
    assert match_arm("group", arms) is None                 # no naming word left
    assert match_arm(None, arms) is None                    # no group stated, two in the registry
    assert match_arm(None, arms[:1]).title == "Drug X"      # no group stated, one in the registry


def test_two_groups_that_both_fit_are_not_matched():
    arms = [arm(0, "Drug X 5 mg"), arm(1, "Drug X 10 mg"), arm(2, "Placebo")]
    assert match_arm("Drug X", arms) is None
    assert match_arm("Drug X 10 mg", arms).title == "Drug X 10 mg"


def test_an_uncertain_group_is_unclear_and_never_compared():
    other_sentence = compare_statement(said(count=42, denominator=101, arm_in_quote=False), registry())
    no_match = compare_statement(said(count=42, denominator=101, arm="trastuzumab"), registry())
    no_group = compare_statement(said(count=42, denominator=101, arm=None, arm_in_quote=None), registry())
    for result in (other_sentence, no_match, no_group):
        assert result.category == "safety_unclear"
        assert result.registry_affected is None


# ---- the registry counterpart ------------------------------------------------

def test_death_count_the_registry_does_not_give():
    safety = registry()
    safety.arms[0].deaths_affected = None
    result = compare_statement(said(kind="death", count=6, denominator=101), safety)
    assert result.category == "safety_in_paper_not_registry"
    assert "not zero" in result.reason


def test_event_spellings_and_rows():
    assert "anaemia" in event_spellings("Anemia")
    assert event_spellings("hiccups") == ["hiccups"]
    events = [event("other", 0, "Neutrophil count decreased", 21, 101), event("other", 1, "Nausea", 30, 101)]
    assert [row.term for row in find_event_rows("neutropenia", events)] == ["Neutrophil count decreased"]


def test_a_named_event_in_one_non_serious_row_is_compared():
    events = [event("other", 0, "Nausea", 30, 101)]
    same = compare_statement(said(kind="event", term="nausea", count=30, denominator=101), registry(events))
    other = compare_statement(said(kind="event", term="nausea", count=35, denominator=101), registry(events))
    assert same.category == "safety_consistent"
    assert other.category == "safety_number_differs"


def test_an_event_the_registry_does_not_name_is_unclear():
    result = compare_statement(said(kind="event", term="hiccups", count=3, denominator=101), registry())
    assert result.category == "safety_unclear"


def test_an_event_in_several_rows_is_not_added_up():
    events = [event("serious", 0, "Nausea", 2, 101), event("other", 0, "Nausea", 30, 101)]
    result = compare_statement(said(kind="event", term="nausea", count=32, denominator=101), registry(events))
    assert result.category == "safety_not_comparable"


def test_a_serious_only_row_is_compared_only_when_every_event_is_listed():
    events = [event("serious", 0, "Sepsis", 2, 101)]
    statement = said(kind="event", term="sepsis", count=2, denominator=101)
    assert compare_statement(statement, registry(events, threshold=5.0)).category == "safety_not_comparable"
    assert compare_statement(statement, registry(events, threshold=None)).category == "safety_not_comparable"
    assert compare_statement(statement, registry(events, threshold=0.0)).category == "safety_consistent"


def test_an_event_row_without_a_number_for_the_group_is_unknown():
    events = [event("other", 0, "Nausea", 30, 101, group_id="EG001")]       # only the placebo group has a number
    result = compare_statement(said(kind="event", term="nausea", count=30, denominator=101), registry(events))
    assert result.category == "unknown"


# ---- unknown is never "no difference" ----------------------------------------

def test_no_registry_data_is_unknown():
    statement = said(count=42, denominator=101)
    assert compare_statement(statement, None).category == "unknown"
    assert compare_statement(statement, SafetyExtraction(status="absent")).category == "unknown"
    assert compare_statement(statement, SafetyExtraction(status="ok")).category == "unknown"


def test_a_rejected_statement_is_never_compared():
    statement = said(count=42, denominator=101, error_code="quote_not_found")
    assert compare_statement(statement, registry()).category == "unknown"


# ---- what the abstract leaves out --------------------------------------------

def test_registry_numbers_the_abstract_does_not_mention():
    statements = [said(kind="death", count=6, denominator=101)]
    found = registry_not_in_abstract(statements, registry())
    assert [(item.category, item.kind) for item in found] == [("safety_registry_not_in_abstract", "serious")]


def test_nothing_is_left_out_when_the_registry_gives_no_count():
    safety = SafetyExtraction(status="ok", arms=[arm(0, "Drug X")])         # no death or serious counts given
    assert registry_not_in_abstract([], safety) == []
    assert registry_not_in_abstract([], None) == []


def test_compare_statements_gives_one_answer_per_statement_plus_the_left_out():
    statements = [said(count=42, denominator=101), said(count=26, denominator=50, arm="placebo")]
    results = compare_statements(statements, registry())
    assert [item.category for item in results] == ["safety_consistent", "safety_consistent", "safety_registry_not_in_abstract"]


def test_no_verdict_words_and_the_window_limit_is_always_said():
    statements = [
        said(count=40, denominator=101), said(count=42, denominator=102), said(count=42),
        said(kind="death", count=6, denominator=101, treatment_related=True), said(count=1, arm="trastuzumab"),
    ]
    for result in compare_statements(statements, registry()):
        text = result.reason.lower()
        for word in ("violation", "wrong", "misreport", "hid", "failed to", "non-compliant", "switch"):
            assert word not in text
        assert "time window" in result.note
