##############################################################################
# trialtrace/pipeline_events.py
#
# PURPOSE: Record structured events for pipeline stages: nct_id, stage, status, error code, duration, analysis (run)
#          ID and a few counts. Each event is written to the log as one JSON line (Cloud Logging reads it when this
#          runs on Google Cloud) and inserted into pipeline_events. A problem while recording never stops the pipeline.
#
#          Two ways to write:
#            normal   on the caller's connection, in a savepoint; the caller commits (the event lives or dies with
#                     the stage's own data)
#            durable  on its OWN short connection, committed at once; used for the "started" event and for the
#                     terminal failure after an unexpected exception, because the caller will roll back and a
#                     normal write would vanish with it
#
#          Crash detection: a "started" event that no completion event points to means the stage has not reported
#          back. That is "completion overdue", not proof of a crash: nothing here can tell a stopped process from a
#          slow one.
#
# USE:     started_id = record_started(conn, nct_id, "papers")
#          started = time.monotonic()
#          ... do the stage ...
#          record_event(conn, nct_id, "papers", "ok", started=started, started_id=started_id, detail={"found": 4})
##############################################################################

import json
import logging
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

STAGES = ("registry", "raw_backup", "papers", "matching")
STATUSES = ("started", "ok", "warning", "failed", "no_eligible")

DURABLE_EVENTS = True            # tests switch this off, so durable writes do not leave committed rows behind

log = logging.getLogger("trialtrace.pipeline")
if not log.handlers:                                     # one JSON line per event on stdout: Cloud Run and Cloud Logging read stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

INSERT_SQL = ("INSERT INTO pipeline_events (nct_id, analysis_id, stage, status, error_code, detail, duration_ms, started_id) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id")


def log_event(event: dict) -> None:
    log.info(json.dumps(event, default=str))


def insert_durable(conn, values: tuple) -> int | None:
    """Insert one event on a separate connection to the same database and commit it. Returns the id, or None if it failed."""
    if not DURABLE_EVENTS:
        return None
    try:
        with psycopg.connect(conn.info.dsn, password=conn.info.password, autocommit=True) as own:
            return own.execute(INSERT_SQL, values).fetchone()[0]
    except Exception as error:
        log.warning("durable pipeline event for %s (%s) was not stored: %s", values[0], values[2], error)
        return None


def record_started(conn, nct_id: str, stage: str, analysis_id: str | None = None) -> int | None:
    """Write a "started" event, durably. Returns its id, or None if it could not be written."""
    log_event({"event": "pipeline_stage", "nct_id": nct_id, "stage": stage, "status": "started", "analysis_id": analysis_id})
    return insert_durable(conn, (nct_id, analysis_id, stage, "started", None, Jsonb({}), None, None))


def record_event(conn, nct_id: str, stage: str, status: str, error_code: str | None = None, detail: dict | None = None,
                 analysis_id: str | None = None, started: float | None = None, started_id: int | None = None,
                 durable: bool = False) -> None:
    """Log and store one completion event. `started` is time.monotonic() from before the stage ran; `started_id` points
    to the stage's "started" event. durable=False writes on the caller's connection (the caller commits); durable=True
    writes on its own connection and commits at once. Never raises."""
    duration_ms = round((time.monotonic() - started) * 1000) if started is not None else None
    detail = detail or {}
    log_event({"event": "pipeline_stage", "nct_id": nct_id, "stage": stage, "status": status, "error_code": error_code,
               "analysis_id": analysis_id, "duration_ms": duration_ms, **detail})
    values = (nct_id, analysis_id, stage, status, error_code, Jsonb(detail), duration_ms, started_id)
    if durable:
        insert_durable(conn, values)
        return
    try:
        with conn.transaction():                                  # a savepoint: a failed insert must not spoil the caller's work
            conn.execute(INSERT_SQL, values)
    except Exception as error:
        log.warning("pipeline event for %s (%s) was not stored: %s", nct_id, stage, error)
