##############################################################################
# tests/test_timeline.py
#
# PURPOSE: Prove the 12-month rule: ranges for partial dates, the exact
#          boundary, the 29 Feb rule, and that bad input gives "not sure".
##############################################################################

from datetime import date, datetime, timezone

import pytest

from trialtrace.checks.timeline import (
    DATE_PATH, TYPE_PATH, check_timeline, older_than_12_months, one_year_later, possible_days,
)
from trialtrace.contracts import Evidence, Snapshot

NCT = "NCT04644237"
HASH = "a" * 64
OLD_HASH = "b" * 64
AS_OF = date(2026, 5, 1)
NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


# --- possible_days: text -> range of real days -----------------------------

def test_full_date_is_one_day():
    assert possible_days("2019-05-14") == (date(2019, 5, 14), date(2019, 5, 14))


def test_month_only_is_the_whole_month():
    assert possible_days("2019-05") == (date(2019, 5, 1), date(2019, 5, 31))


def test_leap_february_has_29_days():
    assert possible_days("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


@pytest.mark.parametrize("raw", ["2019", "2019-13", "2019-02-30", "abc", "", "2019-05-14-01"])
def test_bad_or_vague_text_gives_none(raw):
    assert possible_days(raw) is None


# --- one_year_later: the 29 Feb rule ----------------------------------------

def test_normal_day_moves_one_year():
    assert one_year_later(date(2019, 5, 14)) == date(2020, 5, 14)


def test_29_feb_becomes_1_march():
    assert one_year_later(date(2024, 2, 29)) == date(2025, 3, 1)


def test_28_feb_before_a_leap_year_is_a_normal_day():
    assert one_year_later(date(2023, 2, 28)) == date(2024, 2, 28)   # no special case needed


# --- older_than_12_months ---------------------------------------------------

def test_old_actual_date_is_yes():
    assert older_than_12_months("2019-05-14", "ACTUAL", AS_OF) == "yes"


def test_recent_actual_date_is_no():
    assert older_than_12_months("2026-04-15", "ACTUAL", AS_OF) == "no"


def test_exact_one_year_is_not_older():
    # 2025-05-01 + 1 year = 2026-05-01 = as_of: exactly 12 months, not more
    assert older_than_12_months("2025-05-01", "ACTUAL", AS_OF) == "no"


def test_one_day_past_the_boundary_is_yes():
    assert older_than_12_months("2025-04-30", "ACTUAL", AS_OF) == "yes"


def test_month_entirely_over_a_year_ago_is_yes():
    assert older_than_12_months("2025-04", "ACTUAL", AS_OF) == "yes"


def test_month_straddling_the_line_is_not_sure():
    # 2025-05 spans 1-31 May; as_of 15 May 2026 falls inside that month's anniversary
    assert older_than_12_months("2025-05", "ACTUAL", date(2026, 5, 15)) == "not sure"


def test_estimated_date_is_reported_as_estimated():
    assert older_than_12_months("2019-05-14", "ESTIMATED", AS_OF) == "estimated date"


@pytest.mark.parametrize("date_type", [None, "ANTICIPATED", ""])
def test_missing_or_unexpected_type_is_not_sure(date_type):
    assert older_than_12_months("2019-05-14", date_type, AS_OF) == "not sure"


@pytest.mark.parametrize("raw", [None, "", "2019", "2019-13", "abc"])
def test_missing_or_malformed_date_is_not_sure(raw):
    assert older_than_12_months(raw, "ACTUAL", AS_OF) == "not sure"


# --- check_timeline: the Finding and its evidence ---------------------------

def snapshot(raw_date: str | None, date_type: str | None) -> Snapshot:
    return Snapshot(
        nct_id=NCT, title="Drug X", overall_status="COMPLETED",
        primary_completion_date=raw_date, primary_completion_type=date_type,
        content_hash=HASH, source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=f"data/raw/clinicaltrials/{NCT}/{HASH}.json", retrieved_at=NOW,
    )


def evidence(path: str, content_hash: str = HASH) -> Evidence:
    return Evidence(
        evidence_id=f"{NCT}:{content_hash[:12]}:{path}", nct_id=NCT, content_hash=content_hash,
        source="clinicaltrials", source_url=f"https://clinicaltrials.gov/study/{NCT}",
        field_path=path, value="x", retrieved_at=NOW,
    )


BOTH = [evidence(DATE_PATH), evidence(TYPE_PATH)]


def test_finding_cites_both_date_and_type():
    finding = check_timeline(NCT, snapshot("2019-05", "ACTUAL"), BOTH, AS_OF)
    assert finding.category == "over_12_months"
    assert len(finding.evidence_ids) == 2


def test_within_12_months_category():
    finding = check_timeline(NCT, snapshot("2026-04-15", "ACTUAL"), BOTH, AS_OF)
    assert finding.category == "within_12_months"


def test_estimated_finding_does_not_claim_a_completion():
    finding = check_timeline(NCT, snapshot("2027-01", "ESTIMATED"), BOTH, AS_OF)
    assert finding.category == "estimated_date"
    assert "estimate" in finding.claim


def test_not_sure_becomes_unknown():
    assert check_timeline(NCT, snapshot("2019-13", "ACTUAL"), BOTH, AS_OF).category == "unknown"


def test_no_snapshot_is_unknown():
    assert check_timeline(NCT, None, BOTH, AS_OF).category == "unknown"


def test_two_date_rows_but_no_type_row_is_unknown():
    # Two evidence rows, but the type is uncited: counting rows would wrongly pass
    two_dates = [evidence(DATE_PATH), evidence(DATE_PATH)]
    assert check_timeline(NCT, snapshot("2019-05", "ACTUAL"), two_dates, AS_OF).category == "unknown"


def test_evidence_from_an_older_version_does_not_count():
    old = [evidence(DATE_PATH, OLD_HASH), evidence(TYPE_PATH, OLD_HASH)]
    assert check_timeline(NCT, snapshot("2019-05", "ACTUAL"), old, AS_OF).category == "unknown"
