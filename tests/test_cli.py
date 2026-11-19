##############################################################################
# tests/test_cli.py
#
# PURPOSE: Prove the one piece of logic in the CLI: reading the list of IDs.
##############################################################################

import pytest

from trialtrace.cli import ingest_many, read_ids
from trialtrace.ingest_papers import PaperIngestResult
from trialtrace.ingest_registry import IngestResult


class FakeConnection:
    """Records commit and rollback calls, so we can see what ingest_many did."""

    def __init__(self, rollback_fails=False):
        self.calls = []
        self.rollback_fails = rollback_fails

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        if self.rollback_fails:
            raise ConnectionError("database is gone")
        self.calls.append("rollback")


def fake_ingest(failing_id=None):
    def ingest_one(conn, http, settings, nct_id):
        if nct_id == failing_id:
            raise OSError("disk full")
        return IngestResult(nct_id=nct_id, status="new_version", attempts=1)
    return ingest_one


def test_each_trial_is_committed_after_it_is_ingested():
    conn = FakeConnection()
    results = ingest_many(conn, None, None, ["NCT00000001", "NCT00000002"], fake_ingest())
    assert [r.status for r in results] == ["new_version", "new_version"]
    assert conn.calls == ["commit", "commit"]


def test_an_unexpected_error_is_rolled_back_reported_and_the_batch_goes_on():
    conn = FakeConnection()
    results = ingest_many(conn, None, None, ["NCT00000001", "NCT00000002", "NCT00000003"], fake_ingest("NCT00000002"))
    assert [r.status for r in results] == ["new_version", "error", "new_version"]
    assert results[1].detail == "OSError"
    assert conn.calls == ["commit", "rollback", "commit"]


def test_if_the_rollback_itself_fails_the_batch_stops():
    conn = FakeConnection(rollback_fails=True)
    with pytest.raises(ConnectionError):
        ingest_many(conn, None, None, ["NCT00000001", "NCT00000002"], fake_ingest("NCT00000001"))


def test_the_same_loop_serves_the_papers_ingest():
    def papers_one(conn, http, settings, nct_id):
        if nct_id == "NCT00000002":
            raise OSError("disk full")
        return PaperIngestResult(nct_id=nct_id, status="done", papers_found=2, papers_new=2)

    conn = FakeConnection()
    results = ingest_many(conn, None, None, ["NCT00000001", "NCT00000002"], papers_one, PaperIngestResult)
    assert [r.status for r in results] == ["done", "error"]
    assert isinstance(results[1], PaperIngestResult) and results[1].detail == "OSError"
    assert conn.calls == ["commit", "rollback"]


def test_ids_from_the_command_line_are_kept_in_order():
    assert read_ids(["NCT00000002", "NCT00000001"], None) == ["NCT00000002", "NCT00000001"]


def test_duplicates_and_lowercase_are_cleaned():
    assert read_ids(["nct00000001", "NCT00000001", " NCT00000002 "], None) == ["NCT00000001", "NCT00000002"]


def test_file_lines_are_added_skipping_blanks_and_comments(tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("# my trials\nNCT00000003\n\n  NCT00000004  \n", encoding="utf-8")
    assert read_ids(["NCT00000001"], str(ids_file)) == ["NCT00000001", "NCT00000003", "NCT00000004"]


def test_nothing_given_gives_an_empty_list():
    assert read_ids([], None) == []


def test_match_papers_cli_baseline_is_opt_in(monkeypatch):
    from trialtrace import cli
    calls = []
    monkeypatch.setattr(cli, "run_match_papers", lambda *args: calls.append(args))
    cli.main(["match-papers", "NCT90000001"])
    assert calls[-1][-1] is False
    cli.main(["match-papers", "NCT90000001", "--with-baseline"])
    assert calls[-1][-1] is True
