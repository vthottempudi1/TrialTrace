##############################################################################
# tests/test_registry_changes.py
#
# PURPOSE: Prove question 5a: clear differences are found, matching is cautious,
#          ambiguous items are never called changes, and missing history is unknown.
#          Uses made-up versions; no database.
##############################################################################

from datetime import datetime, timezone

import pytest

from trialtrace.compare.registry_changes import ComparableVersion, compare_outcomes, show_difference, title_key
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, Snapshot

NCT = "NCT04644237"
HASH_OLD, HASH_NEW = "a" * 64, "b" * 64
DAY_OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)
DAY_NEW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def outcome(content_hash, role, position, title, description=None, time_frame=None):
    return RegisteredOutcome(nct_id=NCT, content_hash=content_hash, role=role, position=position,
                             measure_text=title, description=description, time_frame=time_frame)


def version(content_hash, when, outcomes, registry_update=None, status="ok"):
    snapshot = Snapshot(
        nct_id=NCT, title="T", overall_status="COMPLETED", content_hash=content_hash,
        source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="data/x.json",
        retrieved_at=when, registry_last_update=registry_update,
    )
    extraction = None if outcomes is None else OutcomeExtraction(status=status, outcomes=outcomes)
    return ComparableVersion(snapshot=snapshot, outcomes=extraction, captured_at=when)


def old_version(*outcomes, **kw):
    return version(HASH_OLD, DAY_OLD, [outcome(HASH_OLD, *o) for o in outcomes], **kw)


def new_version(*outcomes, **kw):
    return version(HASH_NEW, DAY_NEW, [outcome(HASH_NEW, *o) for o in outcomes], **kw)


def kinds(report):
    return [c.kind for c in report.changes]


# --- no change ---------------------------------------------------------------

def test_same_outcomes_is_no_change_with_evidence():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival")),
                              new_version(("primary", 0, "Overall survival")))
    assert report.status == "no_changes"
    assert report.to_finding().category == "no_registry_outcome_change"
    assert report.to_finding().evidence_ids


def test_capitals_punctuation_and_spacing_do_not_make_a_difference():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall  Survival.", "Time to death", "24 months")),
                              new_version(("primary", 0, "overall survival", "Time to  death", "24  months")))
    assert report.status == "no_changes"


def test_title_key_ignores_capitals_punctuation_and_spaces():
    assert title_key("Overall  Survival (OS)!") == title_key("overall survival os")


# --- clear differences -------------------------------------------------------

def test_role_change_is_found():
    report = compare_outcomes(NCT, old_version(("secondary", 0, "Overall survival")),
                              new_version(("primary", 0, "Overall survival")))
    assert kinds(report) == ["role_changed"]
    assert (report.changes[0].before, report.changes[0].after) == ("secondary", "primary")


def test_time_frame_change_is_found():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival", None, "12 months")),
                              new_version(("primary", 0, "Overall survival", None, "24 months")))
    assert kinds(report) == ["time_frame_changed"]


def test_description_change_is_found():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival", "Time to death")),
                              new_version(("primary", 0, "Overall survival", "Time to progression")))
    assert kinds(report) == ["description_changed"]


def test_an_outcome_added_on_its_own_is_a_clear_change():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival")),
                              new_version(("primary", 0, "Overall survival"), ("secondary", 0, "Safety")))
    assert kinds(report) == ["added"] and report.needs_review == []


def test_an_outcome_removed_on_its_own_is_a_clear_change():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival"), ("secondary", 0, "Safety")),
                              new_version(("primary", 0, "Overall survival")))
    assert kinds(report) == ["removed"] and report.needs_review == []


# --- showing what changed ----------------------------------------------------

def test_show_difference_marks_a_replaced_word():
    assert show_difference("Time to death", "Time to progression") == "Time to [death → progression]"


def test_show_difference_marks_an_added_and_a_removed_word():
    assert show_difference("Time to death", "Time to death or progression") == "Time to death [(nothing) → or progression]"
    assert show_difference("Time to death or progression", "Time to death") == "Time to death [or progression → (nothing)]"


def test_show_difference_keeps_only_a_few_words_around_a_change():
    before = "one two three four five six seven eight nine ten"
    after = "one two three four five six seven eight nine eleven"
    assert show_difference(before, after) == "... seven eight nine [ten → eleven]"


def test_show_difference_handles_a_value_that_was_not_given():
    assert show_difference(None, "24 months") == "(not given) → 24 months"
    assert show_difference("24 months", None) == "24 months → (not given)"


def test_the_finding_shows_which_words_changed():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival", "Time to death", "12 months")),
                              new_version(("primary", 0, "Overall survival", "Time to progression", "24 months")))
    claim = report.to_finding().claim
    assert "description differs: Time to [death → progression]" in claim
    assert "time frame differs: [12 → 24] months" in claim


# --- cautious matching -------------------------------------------------------

def test_a_removed_and_an_added_in_the_same_role_need_review_not_change():
    # One title vanished and another appeared in the same role: may be one outcome reworded
    report = compare_outcomes(NCT, old_version(("primary", 0, "Overall survival")),
                              new_version(("primary", 0, "Time to death")))
    assert report.status == "needs_review"
    assert report.changes == [] and len(report.needs_review) == 2
    finding = report.to_finding()
    assert finding.category == "registry_outcome_needs_review"
    assert "no change is confirmed" in finding.claim


def test_a_duplicate_title_is_ambiguous_and_never_a_change():
    report = compare_outcomes(NCT, old_version(("primary", 0, "Safety"), ("secondary", 0, "Safety")),
                              new_version(("primary", 0, "Safety")))
    assert report.status == "needs_review"
    assert [c.kind for c in report.needs_review] == ["ambiguous"]
    assert report.changes == []


def test_clear_changes_and_review_items_are_reported_separately():
    report = compare_outcomes(
        NCT,
        old_version(("primary", 0, "Overall survival", None, "12 months"), ("secondary", 0, "Response rate")),
        new_version(("primary", 0, "Overall survival", None, "24 months"), ("secondary", 0, "Safety")),
    )
    assert report.status == "changes"
    assert kinds(report) == ["time_frame_changed"]
    assert len(report.needs_review) == 2                        # the swapped secondary outcomes
    claim = report.to_finding().claim
    assert "1 registered outcome difference(s)" in claim and "could not be matched safely" in claim


# --- unknown, never "no change" ----------------------------------------------

def test_no_earlier_version_is_unknown():
    report = compare_outcomes(NCT, None, new_version(("primary", 0, "Overall survival")))
    assert report.status == "unknown" and "earlier version" in report.reason
    assert report.to_finding().category == "unknown"


def test_outcomes_not_extracted_is_unknown():
    report = compare_outcomes(NCT, version(HASH_OLD, DAY_OLD, None), new_version(("primary", 0, "Overall survival")))
    assert report.status == "unknown" and "not extracted" in report.reason


def test_absent_outcomes_section_is_unknown_not_everything_added():
    absent = version(HASH_OLD, DAY_OLD, [], status="absent")
    report = compare_outcomes(NCT, absent, new_version(("primary", 0, "Overall survival")))
    assert report.status == "unknown" and report.changes == []


def test_two_versions_with_no_outcomes_are_unknown():
    report = compare_outcomes(NCT, old_version(), new_version())
    assert report.status == "unknown"


def test_the_same_version_twice_is_an_error():
    same = old_version(("primary", 0, "Overall survival"))
    with pytest.raises(ValueError):
        compare_outcomes(NCT, same, same)


# --- the Finding -------------------------------------------------------------

def test_finding_shows_both_capture_dates_and_registry_dates():
    report = compare_outcomes(
        NCT,
        old_version(("secondary", 0, "Overall survival"), registry_update="2025-01-10"),
        new_version(("primary", 0, "Overall survival"), registry_update="2026-05-20"),
    )
    claim = report.to_finding().claim
    assert "2026-01-01" in claim and "2026-06-01" in claim
    assert "2025-01-10" in claim and "2026-05-20" in claim


def test_finding_for_a_change_cites_evidence_and_uses_no_verdict_words():
    finding = compare_outcomes(NCT, old_version(("secondary", 0, "Overall survival")),
                               new_version(("primary", 0, "Overall survival"))).to_finding()
    assert finding.category == "registry_outcome_change" and finding.evidence_ids
    text = (finding.claim + " ".join(finding.limitations)).lower()
    assert not any(word in text for word in ("violation", "switch", "fraud", "late", "non-compliant"))
