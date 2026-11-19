##############################################################################
# tests/test_results_posted.py
#
# PURPOSE: Prove true / false / unknown map to the right category, that
#          unknown never becomes "no", and that a claim cannot lack evidence.
##############################################################################

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trialtrace.checks.results_posted import check_results_posted
from trialtrace.contracts import Evidence, Finding, Snapshot

NCT = "NCT04644237"
HASH = "a" * 64
OLD_HASH = "b" * 64
NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


def snapshot(has_results: bool | None) -> Snapshot:
    return Snapshot(
        nct_id=NCT, title="Drug X", overall_status="COMPLETED",
        has_results=has_results, content_hash=HASH,
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=f"data/raw/clinicaltrials/{NCT}/{HASH}.json",
        retrieved_at=NOW,
    )


def evidence(content_hash: str = HASH, field_path: str = "hasResults") -> Evidence:
    return Evidence(
        evidence_id=f"{NCT}:{content_hash[:12]}:{field_path}", nct_id=NCT,
        content_hash=content_hash, source="clinicaltrials",
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        field_path=field_path, value="true", retrieved_at=NOW,
    )


def test_true_is_results_available_and_cites_evidence():
    finding = check_results_posted(NCT, snapshot(True), [evidence()])
    assert finding.category == "results_available"
    assert finding.evidence_ids == [f"{NCT}:{HASH[:12]}:hasResults"]


def test_false_is_not_in_snapshot_not_a_judgment():
    finding = check_results_posted(NCT, snapshot(False), [evidence()])
    assert finding.category == "results_not_in_snapshot"
    assert "violation" not in finding.claim.lower()


def test_missing_value_is_unknown_never_no():
    finding = check_results_posted(NCT, snapshot(None), [evidence()])
    assert finding.category == "unknown"


def test_no_snapshot_is_unknown():
    assert check_results_posted(NCT, None, []).category == "unknown"


def test_evidence_from_an_older_version_does_not_count():
    finding = check_results_posted(NCT, snapshot(True), [evidence(content_hash=OLD_HASH)])
    assert finding.category == "unknown"


def test_evidence_for_another_field_does_not_count():
    finding = check_results_posted(NCT, snapshot(True), [evidence(field_path="overallStatus")])
    assert finding.category == "unknown"


def test_a_claim_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        Finding(nct_id=NCT, check="results_posted", category="results_available", claim="Posted.")
