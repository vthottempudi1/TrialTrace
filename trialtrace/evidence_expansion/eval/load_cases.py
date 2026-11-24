##############################################################################
# trialtrace/evidence_expansion/eval/load_cases.py
#
# PURPOSE: Turn one row of the triage sheet into a case the code can run:
#          the registered outcome, the trial title and the linked papers (from the database).
#
# USE:     case = load_case(conn, row)
##############################################################################

import csv

import psycopg

from trialtrace.contracts import RegisteredOutcome
from trialtrace.parse.pubmed_parser import Paper

SHEET_PATH = "outputs/triage/triage_sheet.csv"


def read_sheet() -> list[dict]:
    """All rows of the triage sheet."""
    with open(SHEET_PATH, encoding="utf-8", newline="") as sheet_file:
        return list(csv.DictReader(sheet_file))


def load_case(conn: psycopg.Connection, row: dict) -> dict:
    """One case: the sheet row plus outcome, trial title and linked papers."""
    nct_id = row["nct_id"]
    role = row["role"]
    position = int(row["outcome_position"])

    snapshot = conn.execute(
        "select content_hash, title from latest_snapshots where nct_id = %s", (nct_id,)
    ).fetchone()
    content_hash, trial_title = snapshot

    outcome_row = conn.execute(
        "select measure_text, description, time_frame from registered_outcomes "
        "where nct_id = %s and content_hash = %s and role = %s and position = %s",
        (nct_id, content_hash, role, position),
    ).fetchone()
    outcome = RegisteredOutcome(
        nct_id=nct_id, content_hash=content_hash, role=role, position=position,
        measure_text=outcome_row[0], description=outcome_row[1], time_frame=outcome_row[2],
    )

    linked_papers = []
    for pmid in row["linked_pmids"].split():
        paper_row = conn.execute(
            "select title, abstract from papers where pmid = %s order by retrieved_at desc limit 1", (pmid,)
        ).fetchone()
        if paper_row is not None:
            linked_papers.append(Paper(pmid=pmid, title=paper_row[0], abstract=paper_row[1]))

    return {"id": row["id"], "split": row["split"], "role": role, "v1_status": row["v1_status"],
            "outcome": outcome, "trial_title": trial_title, "linked_papers": linked_papers}
