##############################################################################
# tests/test_reported_outcomes.py
#
# PURPOSE: Prove the results-arm comparison: same outcomes give no differences,
#          each kind of difference is named, anything that cannot be matched safely
#          goes to review, missing data is "unknown" (never "no differences"),
#          findings cite evidence and carry comparison_arm / match_method,
#          and no finding uses loaded words. Synthetic cases, no database.
##############################################################################

from datetime import datetime, timezone

import pytest

from trialtrace.compare.reported_outcomes import compare_registered_to_reported
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, ResultsExtraction, ResultsOutcome, Snapshot

NCT = "NCT04644237"
HASH = "a" * 64
SNAPSHOT = Snapshot(
    nct_id=NCT, title="T", overall_status="COMPLETED", content_hash=HASH, registry_last_update="2025-11-21",
    source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="x", retrieved_at=datetime.now(timezone.utc),
)
LOADED_WORDS = ("violation", "late", "switch", "non-compliant", "omitted", "misconduct", "fraud")


def reg(position, title, role="primary", time_frame="12 months", description=None):
    return RegisteredOutcome(nct_id=NCT, content_hash=HASH, role=role, position=position,
                             measure_text=title, time_frame=time_frame, description=description)


def rep(position, title, result_type="primary", time_frame="12 months", description=None):
    return ResultsOutcome(nct_id=NCT, content_hash=HASH, position=position, measure_text=title,
                          result_type=result_type, time_frame=time_frame, description=description)


def compare(registered=(), reported=()):
    return compare_registered_to_reported(
        SNAPSHOT, OutcomeExtraction(status="ok", outcomes=list(registered)),
        ResultsExtraction(status="ok", outcomes=list(reported)), captured_at="2026-11-04")


def kinds(items):
    return sorted(item.kind for item in items)


# --- no difference ------------------------------------------------------------

def test_the_same_outcomes_give_no_differences():
    result = compare([reg(0, "Overall survival"), reg(1, "Safety", "secondary")],
                     [rep(0, "Overall survival"), rep(1, "Safety", "secondary")])
    assert result.status == "no_differences" and result.differences == [] and result.needs_review == []
    assert result.compared_evidence_ids


def test_capitals_punctuation_and_spacing_do_not_count():
    result = compare([reg(0, "Overall Survival (OS)")], [rep(0, "overall   survival - OS", time_frame="12  months")])
    assert result.status == "no_differences"


def test_descriptions_are_not_compared():
    result = compare([reg(0, "OS", description="Time to death")], [rep(0, "OS", description="A much longer description")])
    assert result.status == "no_differences"


def test_order_does_not_matter():
    result = compare([reg(0, "A"), reg(1, "B", "secondary")], [rep(0, "B", "secondary"), rep(1, "A")])
    assert result.status == "no_differences"


# --- clear differences --------------------------------------------------------

def test_a_registered_outcome_missing_from_the_results_is_registered_only():
    result = compare([reg(0, "OS"), reg(1, "Safety", "secondary")], [rep(0, "OS")])
    assert result.status == "differences" and kinds(result.differences) == ["registered_only"]
    assert result.differences[0].measure_text == "Safety" and result.differences[0].before == "secondary"


def test_a_reported_outcome_that_was_never_registered_is_reported_only():
    result = compare([reg(0, "OS")], [rep(0, "OS"), rep(1, "Response rate", "secondary")])
    assert kinds(result.differences) == ["reported_only"] and result.differences[0].after == "secondary"


def test_a_different_role_is_named():
    result = compare([reg(0, "OS", "primary")], [rep(0, "OS", "secondary")])
    [change] = result.differences
    assert (change.kind, change.before, change.after) == ("role_differs", "primary", "secondary")


def test_a_different_time_frame_is_named():
    result = compare([reg(0, "OS", time_frame="12 months")], [rep(0, "OS", time_frame="24 months")])
    [change] = result.differences
    assert (change.kind, change.before, change.after) == ("time_frame_differs", "12 months", "24 months")


def test_a_time_frame_given_on_one_side_only_is_a_difference():
    result = compare([reg(0, "OS", time_frame=None)], [rep(0, "OS", time_frame="12 months")])
    assert kinds(result.differences) == ["time_frame_differs"]


def test_role_and_time_frame_can_both_differ():
    result = compare([reg(0, "OS", "primary", "12 months")], [rep(0, "OS", "secondary", "24 months")])
    assert kinds(result.differences) == ["role_differs", "time_frame_differs"]


def test_a_post_hoc_outcome_is_not_in_the_plan_by_definition():
    result = compare([reg(0, "OS")], [rep(0, "OS"), rep(1, "Extra analysis", "post_hoc")])
    [change] = result.differences
    assert (change.kind, change.after) == ("reported_only", "post_hoc")


def test_an_extra_post_hoc_next_to_a_missing_registered_outcome_stays_a_clear_difference():
    result = compare([reg(0, "OS"), reg(1, "Safety", "secondary")], [rep(0, "OS"), rep(1, "Extra", "post_hoc")])
    assert kinds(result.differences) == ["registered_only", "reported_only"] and result.needs_review == []


# --- cannot be matched safely: review, never a difference ---------------------

def test_a_missing_and_an_extra_outcome_in_the_same_role_may_be_one_reworded():
    result = compare([reg(0, "Overall survival")], [rep(0, "Time to death from any cause")])
    assert result.status == "needs_review" and result.differences == []
    assert kinds(result.needs_review) == ["registered_only", "reported_only"]


def test_a_missing_and_an_extra_outcome_in_different_roles_are_clear_differences():
    result = compare([reg(0, "Overall survival", "primary")], [rep(0, "Response rate", "secondary")])
    assert result.status == "differences" and kinds(result.differences) == ["registered_only", "reported_only"]


def test_duplicate_registered_titles_are_ambiguous():
    result = compare([reg(0, "OS"), reg(1, "os", "secondary")], [rep(0, "OS")])
    assert result.status == "needs_review" and kinds(result.needs_review) == ["ambiguous"]


def test_duplicate_reported_titles_are_ambiguous():
    result = compare([reg(0, "OS")], [rep(0, "OS"), rep(1, "OS", "secondary")])
    assert kinds(result.needs_review) == ["ambiguous"] and result.differences == []


def test_an_unreadable_reported_type_goes_to_review_not_to_a_role_difference():
    result = compare([reg(0, "OS")], [rep(0, "OS", result_type=None)])
    assert result.status == "needs_review" and kinds(result.needs_review) == ["type_unreadable"]


def test_an_unreadable_type_on_an_unmatched_reported_outcome_goes_to_review():
    result = compare([reg(0, "OS")], [rep(0, "OS"), rep(1, "Mystery", result_type=None)])
    assert kinds(result.needs_review) == ["type_unreadable"] and result.differences == []


def test_differences_and_review_items_are_reported_separately():
    result = compare([reg(0, "OS", time_frame="12 months"), reg(1, "A"), reg(2, "B")],
                     [rep(0, "OS", time_frame="24 months"), rep(1, "A"), rep(2, "B", "primary"), rep(3, "B", "primary")])
    assert result.status == "differences"
    assert kinds(result.differences) == ["time_frame_differs"] and kinds(result.needs_review) == ["ambiguous"]


# --- unknown is never "no differences" ----------------------------------------

def test_registered_outcomes_never_extracted_is_unknown():
    result = compare_registered_to_reported(SNAPSHOT, None, ResultsExtraction(status="ok", outcomes=[rep(0, "OS")]))
    assert result.status == "unknown" and "Registered outcomes were not extracted" in result.reason


def test_reported_outcomes_never_extracted_is_unknown():
    result = compare_registered_to_reported(SNAPSHOT, OutcomeExtraction(status="ok", outcomes=[reg(0, "OS")]), None)
    assert result.status == "unknown" and "posted results were not extracted" in result.reason


def test_no_registered_section_is_unknown():
    result = compare_registered_to_reported(SNAPSHOT, OutcomeExtraction(status="absent"), ResultsExtraction(status="ok", outcomes=[rep(0, "OS")]))
    assert result.status == "unknown" and "no registered outcomes section" in result.reason


def test_no_results_posted_is_not_a_difference_and_not_a_match():
    result = compare_registered_to_reported(SNAPSHOT, OutcomeExtraction(status="ok", outcomes=[reg(0, "OS")]), ResultsExtraction(status="absent"))
    assert result.status == "no_results_posted" and result.differences == [] and result.compared_evidence_ids == []


def test_both_lists_empty_is_unknown_not_a_match():
    result = compare()
    assert result.status == "unknown" and result.reason == "Neither list has any outcomes."


def test_registered_outcomes_with_an_empty_results_list_are_registered_only():
    result = compare([reg(0, "OS")], [])
    assert result.status == "differences" and kinds(result.differences) == ["registered_only"]


# --- findings -----------------------------------------------------------------

def finding_of(registered=(), reported=()):
    return compare(registered, reported).to_finding()


def test_every_finding_names_its_arm_and_method():
    findings = [
        finding_of([reg(0, "OS")], [rep(0, "OS")]),                       # match
        finding_of([reg(0, "OS")], [rep(0, "OS", time_frame="1 mo")]),    # difference
        finding_of([reg(0, "OS")], [rep(0, "Other name")]),               # review
        finding_of(),                                                     # unknown
    ]
    assert [f.category for f in findings] == [
        "reported_outcomes_match", "reported_outcome_differences", "reported_outcomes_need_review", "unknown"]
    for finding in findings:
        assert finding.check == "reported_outcomes"
        assert finding.comparison_arm == "results_submission" and finding.match_method == "deterministic"


def test_findings_cite_evidence_from_both_sides():
    finding = finding_of([reg(0, "OS")], [rep(0, "OS", time_frame="1 month")])
    assert any("protocolSection.outcomesModule.primaryOutcomes.0" in i for i in finding.evidence_ids)
    assert any("resultsSection.outcomeMeasuresModule.outcomeMeasures.0" in i for i in finding.evidence_ids)


def test_unknown_findings_carry_no_evidence_but_say_why():
    finding = compare_registered_to_reported(SNAPSHOT, OutcomeExtraction(status="ok"), ResultsExtraction(status="absent")).to_finding()
    assert finding.category == "unknown" and finding.evidence_ids == []
    assert any("No results outcome measures are posted" in text for text in finding.limitations)


def test_the_claim_names_the_version_and_the_limits_say_what_is_not_compared():
    finding = finding_of([reg(0, "OS")], [rep(0, "OS")])
    assert "captured 2026-11-04" in finding.claim and "registry update 2025-11-21" in finding.claim
    assert any("original plan" in text for text in finding.limitations)
    assert any("Descriptions are not compared" in text for text in finding.limitations)


@pytest.mark.parametrize("registered, reported", [
    ([reg(0, "OS")], [rep(0, "OS")]),
    ([reg(0, "OS"), reg(1, "B", "secondary")], [rep(0, "OS", "secondary", "9 mo"), rep(1, "Z", "post_hoc")]),
    ([reg(0, "OS")], [rep(0, "Time to death")]),
    ([reg(0, "OS"), reg(1, "os")], [rep(0, "OS", result_type=None)]),
    ([reg(0, "OS")], []),
])
def test_no_loaded_words_in_any_claim(registered, reported):
    finding = finding_of(registered, reported)
    text = (finding.claim + " " + " ".join(finding.limitations)).lower()
    assert not any(word in text for word in LOADED_WORDS)
