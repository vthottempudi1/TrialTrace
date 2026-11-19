##############################################################################
# tests/test_raw_store.py
#
# PURPOSE: Prove the hash ignores the daily-changing field, notices real
#          changes, and never overwrites a saved file.
##############################################################################

import json
from datetime import datetime, timezone

from trialtrace.sources.raw_store import compute_content_hash, save_raw


def record(version: str, title: str = "Drug X") -> bytes:
    """A tiny registry record. Only the version stamp and title can differ."""
    data = {
        "title": title,
        "derivedSection": {"miscInfoModule": {"versionHolder": version}},
    }
    return json.dumps(data).encode("utf-8")


def test_volatile_field_does_not_change_hash():
    # Same trial downloaded on two days: only the version stamp differs
    one = compute_content_hash(record("2026-01-01"), "clinicaltrials")
    two = compute_content_hash(record("2026-01-02"), "clinicaltrials")
    assert one == two


def test_real_change_gives_new_hash():
    one = compute_content_hash(record("2026-01-01", title="Drug X"), "clinicaltrials")
    two = compute_content_hash(record("2026-01-01", title="Drug Y"), "clinicaltrials")
    assert one != two


def test_saving_twice_keeps_the_first_file(tmp_path):
    when = datetime.now(timezone.utc)
    url = "https://clinicaltrials.gov/study/NCT04644237"

    first = save_raw("NCT04644237", url, when, record("2026-01-01"), tmp_path)
    # Same content, different stamp: same file name, so the first file must stay
    second = save_raw("NCT04644237", url, when, record("2026-01-02"), tmp_path)

    assert first.raw_file_path == second.raw_file_path
    saved = (tmp_path / "raw/clinicaltrials/NCT04644237" / f"{first.content_hash}.json").read_bytes()
    assert saved == record("2026-01-01")
