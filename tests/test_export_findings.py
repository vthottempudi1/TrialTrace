##############################################################################
# tests/test_export_findings.py
#
# PURPOSE: Prove the export keeps the model finding and the review apart, fills the final status only for
#          Human-verified findings, protects spreadsheet cells, and covers the same rows as the queue.
##############################################################################

import csv
import io
from datetime import datetime, timezone

from tests.test_match_review import NCT, add_finding
from trialtrace.review.export_findings import COLUMNS, export_rows, safe_cell, to_csv, to_markdown
from trialtrace.review.match_review import load_queue, save_review


def queue_row(**changes):
    row = {"nct_id": NCT, "outcome_role": "primary", "outcome_position": 0, "outcome_text": "Mortality", "pmid": "111",
           "status": "identified", "matched_name": "Mortality", "quote_text": "mortality fell", "reason": None,
           "model": "m", "served_model": "m-1", "prompt_hash": "p" * 12, "schema_version": "v2", "registry_hash": "r" * 64,
           "paper_raw_hash": "q" * 64, "match_run_id": "match-1", "review_id": None, "decision": None, "reviewer": None,
           "note": None, "reviewed_at": None, "corrected_status": None, "corrected_matched_name": None, "state": "needs_review"}
    row.update(changes)
    return row


def reviewed(**changes):
    base = {"review_id": 1, "reviewer": "Dr Rao", "reviewed_at": datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)}
    base.update(changes)
    return queue_row(**base)


def test_a_new_finding_has_no_review_and_no_final_status():
    [row] = export_rows([queue_row()])
    assert row["review_state"] == "Needs review" and row["final_status"] == "" and row["decision"] == "" and row["reviewer"] == ""


def test_accepted_finding_gets_the_model_status_as_final():
    [row] = export_rows([reviewed(decision="accept", state="human_verified")])
    assert row["final_status"] == "identified" and row["model_status"] == "identified" and row["reviewer"] == "Dr Rao"


def test_accepted_correction_gets_the_corrected_status_and_the_model_answer_stays_visible():
    [row] = export_rows([reviewed(decision="accept_correction", state="human_verified", corrected_status="not_identified", corrected_matched_name="")])
    assert row["final_status"] == "not_identified" and row["model_status"] == "identified"


def test_pending_correction_and_unresolved_have_no_final_status():
    pending, unresolved = export_rows([reviewed(decision="correct", corrected_status="ambiguous", note="why"),
                                       reviewed(decision="undetermined", state="unresolved", note="too short")])
    assert pending["final_status"] == "" and pending["review_state"] == "Needs review" and pending["corrected_status"] == "ambiguous"
    assert unresolved["final_status"] == "" and unresolved["review_state"] == "Unresolved"


def test_cells_that_could_run_as_formulas_are_protected():
    assert safe_cell("=SUM(A1)") == "'=SUM(A1)" and safe_cell("-5 mg") == "'-5 mg" and safe_cell("plain") == "plain"
    [row] = export_rows([queue_row(quote_text="=HYPERLINK(1)")])
    assert "'=HYPERLINK(1)" in to_csv([row])


def test_csv_has_one_line_per_finding_with_the_documented_columns():
    rows = export_rows([queue_row(), queue_row(outcome_position=1, outcome_text='Pain, "severe"')])
    parsed = list(csv.DictReader(io.StringIO(to_csv(rows))))
    assert len(parsed) == 2 and tuple(parsed[0].keys()) == COLUMNS and parsed[1]["registered_outcome"] == 'Pain, "severe"'


def test_markdown_names_the_counts_and_the_caution():
    rows = export_rows([queue_row(), reviewed(decision="accept", state="human_verified", outcome_position=1)])
    text = to_markdown(rows, NCT)
    assert "Needs review 1" in text and "Human-verified 1" in text and "provisional" in text and "Final (human-verified): identified" in text


def test_export_covers_the_queue_from_the_database(conn):
    a = add_finding(conn, input_hash="h1", position=0)
    add_finding(conn, input_hash="h2", position=1)
    save_review(conn, a, "Dr Rao", "accept")
    rows = export_rows(load_queue(conn, NCT))
    assert [row["review_state"] for row in rows] == ["Human-verified", "Needs review"] and rows[0]["final_status"] == "identified"
