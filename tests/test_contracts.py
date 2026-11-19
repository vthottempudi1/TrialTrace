##############################################################################
# tests/test_contracts.py
#
# PURPOSE: Prove the contract rules hold. Each rejection test starts from
#          valid data and breaks exactly one thing.
##############################################################################

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trialtrace.contracts import Snapshot


def valid_snapshot(**changes) -> dict:
    """A valid Snapshot as a dict. Pass changes to break one field."""
    data = {
        "nct_id": "NCT04644237",
        "title": "Drug X in lung cancer",
        "overall_status": "COMPLETED",
        "content_hash": "a" * 64,
        "source_url": "https://clinicaltrials.gov/study/NCT04644237",
        "raw_file_path": "data/raw/clinicaltrials/NCT04644237/x.json",
        "retrieved_at": datetime.now(timezone.utc),
    }
    data.update(changes)
    return data


def test_valid_snapshot_is_accepted():
    assert Snapshot(**valid_snapshot()).nct_id == "NCT04644237"


def test_missing_has_results_stays_unknown():
    # Unknown must stay None, never become False
    assert Snapshot(**valid_snapshot()).has_results is None


def test_bad_nct_id_is_rejected():
    with pytest.raises(ValidationError):
        Snapshot(**valid_snapshot(nct_id="NCT123"))


def test_bad_hash_is_rejected():
    # 64 characters long, but uppercase: only the character rule is broken
    with pytest.raises(ValidationError):
        Snapshot(**valid_snapshot(content_hash="A" * 64))


def test_unknown_completion_type_is_rejected():
    with pytest.raises(ValidationError):
        Snapshot(**valid_snapshot(primary_completion_type="ANTICIPATED"))
