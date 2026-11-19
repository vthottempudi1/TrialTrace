##############################################################################
# trialtrace/safety/backfill_safety.py
#
# PURPOSE: Fill in the adverse-event data of snapshots saved BEFORE the safety tables existed, from the raw files
#          already on disk. No network. Only snapshots whose safety_status is still unknown (NULL) are touched, and
#          stored answers are never overwritten (save_safety_rows only fills in).
#          A missing file or a file that cannot be parsed is counted and left unknown, never guessed.
#          The caller commits.
#
#          backfill_safety_evidence adds the evidence rows the safety finding cites (step S4), from the stored rows.
#
# USE:     result = backfill_safety(conn)       # BackfillResult(filled, absent, missing_file, parse_error)
#          added = backfill_safety_evidence(conn)
#          added = backfill_paper_evidence(conn)     # abstract and quote evidence under the IDs that hold the PMID
##############################################################################

from dataclasses import dataclass
from pathlib import Path

import psycopg

from trialtrace.safety.safety_registry import safety_evidence
from trialtrace.compare.paper_outcomes import evidence_for_paper
from trialtrace.db.repository import get_paper_matches, get_papers_for_trial, get_safety, save_evidence, save_safety_rows
from trialtrace.parse.registry_parser import ParseError
from trialtrace.safety.safety_parser import parse_safety
from trialtrace.safety.read_safety import save_safety_evidence
from trialtrace.sources.raw_store import StoredRaw


@dataclass
class BackfillResult:
    filled: int = 0          # snapshots that now have a safety module
    absent: int = 0          # snapshots whose record has no safety module (stored as absent)
    missing_file: int = 0    # raw file not on disk: stays unknown
    parse_error: int = 0     # raw file unreadable: stays unknown


def backfill_safety(conn: psycopg.Connection) -> BackfillResult:
    result = BackfillResult()
    rows = conn.execute(
        "SELECT nct_id, content_hash, source_url, raw_file_path, retrieved_at FROM snapshots "
        "WHERE safety_status IS NULL ORDER BY nct_id, retrieved_at"
    ).fetchall()
    for nct_id, content_hash, source_url, raw_file_path, retrieved_at in rows:
        path = Path(raw_file_path)
        if not path.exists():
            result.missing_file += 1
            continue
        stored = StoredRaw(nct_id=nct_id, url=source_url, retrieved_at=retrieved_at,
                           content_hash=content_hash, raw_file_path=raw_file_path)
        try:
            safety = parse_safety(path.read_bytes(), stored)
        except ParseError:
            result.parse_error += 1
            continue
        save_safety_rows(conn, nct_id, content_hash, safety)
        if safety.status == "absent":
            result.absent += 1
        else:
            result.filled += 1
    return result


def backfill_safety_evidence(conn: psycopg.Connection) -> int:
    """Add the evidence rows the safety finding cites, for every stored version with a safety module.

    Built from the stored safety rows (no files, no network). Rows already there are left alone,
    so running it again adds nothing. Returns how many rows were new. The caller commits.
    """
    rows = conn.execute(
        "SELECT nct_id, content_hash, retrieved_at FROM snapshots WHERE safety_status = 'ok' ORDER BY nct_id, retrieved_at"
    ).fetchall()
    added = 0
    for nct_id, content_hash, retrieved_at in rows:
        safety = get_safety(conn, nct_id, content_hash)
        added += save_evidence(conn, safety_evidence(safety, retrieved_at))
    return added


def backfill_paper_evidence(conn: psycopg.Connection) -> int:
    """Save the abstract and quote evidence of every stored match run and safety reading again.

    Needed once after the evidence ID gained the PMID (D53): older rows have IDs without it. Built from the
    stored rows (no files, no network, no model). Rows already there are left alone, so running it again
    adds nothing. Returns how many rows were new. The caller commits.
    """
    added = 0
    for (nct_id,) in conn.execute("SELECT DISTINCT nct_id FROM paper_matches ORDER BY nct_id").fetchall():
        rows = get_paper_matches(conn, nct_id)              # the newest run: the one the report reads
        for stored in get_papers_for_trial(conn, nct_id):
            paper_rows = []
            for row in rows:
                if row["pmid"] == stored["pmid"] and row["paper_raw_hash"] == stored["raw_hash"]:
                    paper_rows.append(row)
            if paper_rows and stored["abstract"]:
                added += save_evidence(conn, evidence_for_paper(nct_id, stored, paper_rows))
    for (nct_id,) in conn.execute("SELECT DISTINCT nct_id FROM safety_readings ORDER BY nct_id").fetchall():
        added += save_safety_evidence(conn, nct_id)
    return added
