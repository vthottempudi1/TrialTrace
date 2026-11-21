##############################################################################
# tests/test_pipeline_status.py
#
# PURPOSE: Prove the pipeline status lines keep "not started", "nothing to read", "failed" and "awaiting review"
#          apart, that an event write never stops the pipeline, and that events are stored with their run ID.
##############################################################################

from datetime import datetime, timezone

from trialtrace.pipeline_events import record_event
from trialtrace.pipeline_status import backup_line, matching_line, papers_line, pipeline_status, registry_line

NCT = "NCT09999999"


def states(conn, nct_id=NCT):
    return {item["stage"]: item["state"] for item in pipeline_status(conn, nct_id)}


def search(conn, status, batches_failed=0, error_code=None, found=2):
    conn.execute(
        "INSERT INTO paper_searches (nct_id, query, searched_at, status, pmids_found, attempts, batches_total, batches_failed, error_code) "
        "VALUES (%s, 'q', %s, %s, %s, 1, 1, %s, %s)", (NCT, datetime.now(timezone.utc), status, found, batches_failed, error_code))


def test_unknown_trial_is_not_started_everywhere(conn):
    assert states(conn) == {"Registry": "not_started", "Raw backup": "not_recorded", "Papers": "not_started",
                            "Matching": "not_started", "Findings": "not_started", "Review": "not_started"}


def test_a_failed_download_is_shown_as_failed_with_its_code(conn):
    conn.execute("INSERT INTO ingest_failures (nct_id, step, error_code, attempts) VALUES (%s, 'fetch', 'not_found', 1)", (NCT,))
    line = registry_line(conn, NCT)
    assert line["state"] == "failed" and "not_found" in line["summary"]


def test_papers_not_found_is_nothing_to_read_not_a_failure(conn):
    search(conn, "not_found", found=0)
    assert papers_line(conn, NCT)["state"] == "no_eligible"


def test_papers_search_unavailable_is_a_failure(conn):
    search(conn, "unavailable", error_code="timeout", found=0)
    line = papers_line(conn, NCT)
    assert line["state"] == "failed" and "timeout" in line["summary"]


def test_failed_batches_are_a_warning(conn):
    search(conn, "ok", batches_failed=1, error_code="ParseError")
    assert papers_line(conn, NCT)["state"] == "warning"


def test_raw_backup_event_ok_and_warning(conn):
    record_event(conn, NCT, "raw_backup", "ok", detail={"source": "clinicaltrials"})
    assert backup_line(conn, NCT)["state"] == "ok"
    record_event(conn, NCT, "raw_backup", "warning", error_code="upload_failed")
    assert backup_line(conn, NCT)["state"] == "warning"           # the newest attempt is the one shown


def test_event_stores_run_id_duration_and_detail(conn):
    record_event(conn, NCT, "matching", "ok", analysis_id="match-1", started=0.0, detail={"pairs": 4})
    row = conn.execute("SELECT analysis_id, duration_ms, detail FROM pipeline_events WHERE nct_id = %s", (NCT,)).fetchone()
    assert row[0] == "match-1" and row[1] is not None and row[2] == {"pairs": 4}


def test_a_broken_event_write_never_stops_the_pipeline():
    class Broken:
        def transaction(self):
            raise RuntimeError("database gone")
    record_event(Broken(), NCT, "papers", "ok")                    # must not raise


def test_matching_with_no_rows_and_a_no_eligible_event(conn):
    record_event(conn, NCT, "matching", "no_eligible", analysis_id="match-2")
    line = matching_line(conn, NCT, None, [], {"state": "ok"})
    assert line["state"] == "no_eligible" and line["run_id"] == "match-2"


def test_matching_failed_pairs_are_a_warning_and_all_failed_is_failed(conn):
    when = datetime.now(timezone.utc)
    rows = [{"status": "identified", "error_code": None, "created_at": when},
            {"status": "failed", "error_code": "quote_not_found", "created_at": when}]
    warning = matching_line(conn, NCT, "match-3", rows, {"state": "ok"})
    assert warning["state"] == "warning" and "quote_not_found" in warning["summary"]
    failed = matching_line(conn, NCT, "match-3", rows[1:], {"state": "ok"})
    assert failed["state"] == "failed"


# ---- crash detection: a "started" event that nothing points to ----

def start_row(conn, stage, minutes_ago, analysis_id=None):
    return conn.execute(
        "INSERT INTO pipeline_events (nct_id, analysis_id, stage, status, created_at) "
        "VALUES (%s, %s, %s, 'started', now() - make_interval(mins => %s)) RETURNING id",
        (NCT, analysis_id, stage, minutes_ago)).fetchone()[0]


def test_old_start_without_completion_is_overdue_not_a_proven_crash(conn):
    start_row(conn, "papers", minutes_ago=60)
    line = [item for item in pipeline_status(conn, NCT) if item["stage"] == "Papers"][0]
    assert line["state"] == "overdue" and "may still be working" in line["summary"]


def test_recent_start_without_completion_is_running(conn):
    start_row(conn, "matching", minutes_ago=1, analysis_id="match-9")
    line = [item for item in pipeline_status(conn, NCT) if item["stage"] == "Matching"][0]
    assert line["state"] == "running" and line["run_id"] == "match-9"


def test_start_with_a_completion_is_not_overdue(conn):
    started_id = start_row(conn, "papers", minutes_ago=60)
    record_event(conn, NCT, "papers", "ok", started_id=started_id)
    line = [item for item in pipeline_status(conn, NCT) if item["stage"] == "Papers"][0]
    assert line["state"] not in ("overdue", "running")


def test_the_started_event_is_committed_on_its_own_connection(conn, database_url, monkeypatch):
    import psycopg
    from trialtrace.pipeline_events import record_started
    monkeypatch.setattr("trialtrace.pipeline_events.DURABLE_EVENTS", True)
    started_id = record_started(conn, "NCT09999998", "registry")
    try:
        with psycopg.connect(database_url) as other:                       # a different connection can already see it
            seen = other.execute("SELECT status FROM pipeline_events WHERE id = %s", (started_id,)).fetchone()
        assert seen == ("started",)
    finally:
        with psycopg.connect(database_url, autocommit=True) as cleaner:
            cleaner.execute("DELETE FROM pipeline_events WHERE nct_id = 'NCT09999998'")


def test_an_old_crash_is_history_once_a_later_attempt_finished(conn):
    start_row(conn, "papers", minutes_ago=120)                       # crashed, never completed
    later = start_row(conn, "papers", minutes_ago=30)
    record_event(conn, NCT, "papers", "ok", started_id=later)        # a later attempt finished fine
    line = [item for item in pipeline_status(conn, NCT) if item["stage"] == "Papers"][0]
    assert line["state"] not in ("overdue", "running")


def test_a_failure_after_an_exception_survives_the_callers_rollback(conn, database_url, monkeypatch):
    """The stage raises, the caller rolls back; the failed event must still be there, so the start is not 'overdue'."""
    import psycopg
    import pytest
    from trialtrace import ingest_papers as module
    from trialtrace.config import Settings

    nct = "NCT09999996"
    monkeypatch.setattr("trialtrace.pipeline_events.DURABLE_EVENTS", True)

    def crash(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(module, "_ingest_papers", crash)
    try:
        with pytest.raises(RuntimeError):
            module.ingest_papers(conn, None, Settings(), nct)
        conn.rollback()                                            # what a real caller does after the exception
        with psycopg.connect(database_url) as other:
            rows = other.execute("SELECT status, error_code, started_id FROM pipeline_events WHERE nct_id = %s ORDER BY id", (nct,)).fetchall()
            line = [item for item in pipeline_status(other, nct) if item["stage"] == "Papers"][0]
        assert [row[0] for row in rows] == ["started", "failed"] and rows[1][1] == "RuntimeError" and rows[1][2] is not None
        assert line["state"] not in ("overdue", "running")
    finally:
        with psycopg.connect(database_url, autocommit=True) as cleaner:
            cleaner.execute("DELETE FROM pipeline_events WHERE nct_id = %s", (nct,))


def test_all_unusable_answers_make_findings_failed_and_review_not_started():
    from trialtrace.pipeline_status import findings_line
    when = datetime.now(timezone.utc)
    rows = [{"status": "failed", "error_code": "AuthenticationError", "created_at": when}]
    assert findings_line(rows, "match-5")["state"] == "failed"
    mixed = rows + [{"status": "identified", "error_code": None, "created_at": when}]
    assert findings_line(mixed, "match-5")["state"] == "warning"


def test_review_with_no_usable_findings_is_not_ok(conn):
    from trialtrace.pipeline_status import review_line
    assert review_line(conn, NCT, True)["state"] == "not_started"
