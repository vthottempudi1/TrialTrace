##############################################################################
# tests/test_match_review.py
#
# PURPOSE: Prove the review of model findings follows the rules: the model finding is never changed,
#          a correction is verified only after explicit acceptance, "undetermined" stays unresolved,
#          a changed model answer needs review again. Uses the test database; every test is rolled back.
##############################################################################

import psycopg
import pytest

from trialtrace.review.match_review import count_states, counts_by_trial, load_by_match_ids, load_queue, save_assisted_review, save_review

NCT = "NCT04644237"


def add_finding(conn, run_id="match-1", status="identified", quote="mortality fell", input_hash="h1", position=0):
    row = conn.execute(
        "INSERT INTO paper_matches (match_run_id, nct_id, registry_hash, outcome_position, outcome_role, outcome_text, pmid, "
        "paper_raw_hash, system, model, status, matched_name, quote_text, quote_start, quote_end, input_hash, served_model) "
        "VALUES (%s, %s, %s, %s, 'primary', 'Mortality', '111', %s, 'llm', 'test-model', %s, 'Mortality', %s, %s, %s, %s, 'test-served') RETURNING match_id",
        (run_id, NCT, "r" * 64, position, "p" * 64, status, quote, 0 if quote else None, len(quote) if quote else None, input_hash),
    ).fetchone()
    return row[0]


def state_of(conn):
    return load_queue(conn, NCT)[0]["state"]


def test_new_finding_needs_review(conn):
    add_finding(conn)
    assert state_of(conn) == "needs_review"


def test_accept_makes_it_human_verified_and_records_versions(conn):
    match_id = add_finding(conn)
    save_review(conn, match_id, "Dr Rao", "accept", "Quote supports it")
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified"
    assert row["reviewer"] == "Dr Rao" and row["reviewed_at"] is not None
    stored = conn.execute("SELECT model, served_model, registry_hash, paper_raw_hash FROM paper_match_reviews").fetchone()
    assert stored == ("test-model", "test-served", "r" * 64, "p" * 64)


def test_review_never_changes_the_model_finding(conn):
    match_id = add_finding(conn)
    before = conn.execute("SELECT status, matched_name, quote_text FROM paper_matches WHERE match_id = %s", (match_id,)).fetchone()
    save_review(conn, match_id, "Dr Rao", "correct", "Abstract reports it", corrected_status="not_identified")
    after = conn.execute("SELECT status, matched_name, quote_text FROM paper_matches WHERE match_id = %s", (match_id,)).fetchone()
    assert before == after


def test_correction_is_verified_only_after_explicit_acceptance(conn):
    match_id = add_finding(conn)
    save_review(conn, match_id, "Dr Rao", "correct", "Different outcome", corrected_status="not_identified")
    assert state_of(conn) == "needs_review"
    save_review(conn, match_id, "Dr Rao", "accept_correction")
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified" and row["corrected_status"] == "not_identified"


def test_accept_correction_without_a_pending_correction_is_refused(conn):
    match_id = add_finding(conn)
    with pytest.raises(ValueError, match="pending correction"):
        save_review(conn, match_id, "Dr Rao", "accept_correction")
    save_review(conn, match_id, "Dr Rao", "accept")
    with pytest.raises(ValueError, match="pending correction"):
        save_review(conn, match_id, "Dr Rao", "accept_correction")


def test_undetermined_stays_unresolved_and_needs_a_note(conn):
    match_id = add_finding(conn)
    with pytest.raises(ValueError, match="note"):
        save_review(conn, match_id, "Dr Rao", "undetermined")
    save_review(conn, match_id, "Dr Rao", "undetermined", "Abstract too short", unresolved_reason="abstract_too_short")
    assert state_of(conn) == "unresolved"


def test_correction_needs_status_and_note(conn):
    match_id = add_finding(conn)
    with pytest.raises(ValueError, match="status"):
        save_review(conn, match_id, "Dr Rao", "correct", "why")
    with pytest.raises(ValueError, match="note"):
        save_review(conn, match_id, "Dr Rao", "correct", "", corrected_status="ambiguous")


def test_reviewer_is_required_and_decision_must_be_known(conn):
    match_id = add_finding(conn)
    with pytest.raises(ValueError, match="reviewer"):
        save_review(conn, match_id, " ", "accept")
    with pytest.raises(ValueError, match="Unknown"):
        save_review(conn, match_id, "Dr Rao", "approve")


def test_database_refuses_correction_without_status(conn):
    match_id = add_finding(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO paper_match_reviews (match_id, input_hash, decision, reviewer, note, reviewed_status, registry_hash, paper_raw_hash) "
                     "VALUES (%s, 'h1', 'correct', 'x', 'why', 'identified', 'r', 'p')", (match_id,))


def test_review_survives_a_rerun_that_reuses_the_answer(conn):
    first = add_finding(conn, run_id="match-1")
    save_review(conn, first, "Dr Rao", "accept")
    add_finding(conn, run_id="match-2")                      # same question, same answer, new run
    row = load_queue(conn, NCT)[0]
    assert row["match_run_id"] == "match-2" and row["state"] == "human_verified"


def test_a_different_answer_to_the_same_question_needs_review_again(conn):
    first = add_finding(conn, run_id="match-1")
    save_review(conn, first, "Dr Rao", "accept")
    add_finding(conn, run_id="match-2", status="not_identified", quote=None)
    assert state_of(conn) == "needs_review"


def test_counts(conn):
    a = add_finding(conn, input_hash="h1", position=0)
    b = add_finding(conn, input_hash="h2", position=1)
    add_finding(conn, input_hash="h3", position=2)
    save_review(conn, a, "Dr Rao", "accept")
    save_review(conn, b, "Dr Rao", "undetermined", "unclear", unresolved_reason="other")
    assert count_states(load_queue(conn, NCT)) == {"needs_review": 1, "human_verified": 1, "unresolved": 1}


def test_counts_by_trial_and_across_trials(conn):
    a = add_finding(conn, input_hash="h1", position=0)
    add_finding(conn, input_hash="h2", position=1)
    other = conn.execute(
        "INSERT INTO paper_matches (match_run_id, nct_id, registry_hash, outcome_position, outcome_role, outcome_text, pmid, "
        "paper_raw_hash, system, status, input_hash) VALUES ('match-1', 'NCT00000001', %s, 0, 'primary', 'Pain', '222', %s, 'llm', 'not_identified', 'h9') RETURNING match_id",
        ("r" * 64, "q" * 64)).fetchone()[0]
    save_review(conn, a, "Dr Rao", "accept")
    save_review(conn, other, "Dr Rao", "undetermined", "unclear", unresolved_reason="other")
    everything = [row for row in load_queue(conn) if row["nct_id"] in (NCT, "NCT00000001")]
    lines = {line["nct_id"]: line for line in counts_by_trial(everything)}
    assert lines[NCT] == {"nct_id": NCT, "needs_review": 1, "human_verified": 1, "unresolved": 0, "total": 2}
    assert lines["NCT00000001"]["unresolved"] == 1
    assert count_states(everything) == {"needs_review": 1, "human_verified": 1, "unresolved": 1}


# --- assisted review of unclear findings (the reviewer sees the model's answer) -------------------------

def unclear(conn, **kwargs):
    return add_finding(conn, status="ambiguous", quote=None, **kwargs)


def test_settling_an_unclear_finding_is_verified_with_evidence_and_time(conn):
    match_id = unclear(conn)
    save_assisted_review(conn, match_id, "Venu", "identified", evidence="sentence 9 gives the result", seconds=75)
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified" and row["corrected_status"] == "identified" and row["decision"] == "accept_correction"
    stored = conn.execute("SELECT evidence, review_seconds FROM paper_match_reviews ORDER BY id DESC LIMIT 1").fetchone()
    assert stored == ("sentence 9 gives the result", 75)
    assert row["status"] == "ambiguous"                                     # the model's answer is untouched


def test_settling_needs_evidence(conn):
    with pytest.raises(ValueError, match="relied on"):
        save_assisted_review(conn, unclear(conn), "Venu", "not_identified", evidence="  ")


def test_agreeing_that_it_is_unclear_needs_evidence_and_a_listed_reason(conn):
    match_id = unclear(conn)
    with pytest.raises(ValueError, match="what the abstract says"):
        save_assisted_review(conn, match_id, "Venu", "still_unclear", reason="generic_statement")
    with pytest.raises(ValueError, match="why it stays unclear"):
        save_assisted_review(conn, match_id, "Venu", "still_unclear", evidence="s4 generic", reason="because")
    save_assisted_review(conn, match_id, "Venu", "still_unclear", evidence="s4 generic", reason="generic_statement", seconds=40)
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified" and row["decision"] == "accept"
    assert conn.execute("SELECT unresolved_reason FROM paper_match_reviews").fetchone()[0] == "generic_statement"


def test_unable_stays_unresolved_with_reason_and_note(conn):
    match_id = unclear(conn)
    with pytest.raises(ValueError, match="reason from the list"):
        save_assisted_review(conn, match_id, "Venu", "unable", note="cannot tell")
    with pytest.raises(ValueError, match="note"):
        save_assisted_review(conn, match_id, "Venu", "unable", reason="abstract_too_short")
    save_assisted_review(conn, match_id, "Venu", "unable", reason="abstract_too_short", note="cut off")
    assert state_of(conn) == "unresolved"


def test_assisted_review_is_only_for_unclear_findings(conn):
    with pytest.raises(ValueError, match="called unclear"):
        save_assisted_review(conn, add_finding(conn), "Venu", "identified", evidence="x")


def test_a_frozen_selection_is_loaded_by_match_id_in_the_given_order_even_after_a_newer_run(conn):
    first = unclear(conn, run_id="match-1", input_hash="h1", position=0)
    second = unclear(conn, run_id="match-1", input_hash="h2", position=1)
    unclear(conn, run_id="match-2", input_hash="h1", position=0)              # a newer run exists
    rows = load_by_match_ids(conn, [second, first])
    assert [row["match_id"] for row in rows] == [second, first] and all(row["state"] == "needs_review" for row in rows)


# --- AI reviewers: reviewed and shown, but never "Human-verified" and never over a human review ---------------

AI = "Claude (AI)"


def test_an_ai_review_never_makes_a_finding_human_verified_and_is_still_shown(conn):
    match_id = unclear(conn)
    save_assisted_review(conn, match_id, AI, "not_identified", evidence="s3 says nothing about it")
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "needs_review" and row["review_id"] is None             # no human review exists
    assert row["ai_reviewer"] == AI and row["ai_decision"] == "accept_correction" and row["ai_corrected_status"] == "not_identified"
    assert row["ai_evidence"] == "s3 says nothing about it"


def test_a_human_review_after_an_ai_review_counts_and_the_ai_review_stays_visible(conn):
    match_id = unclear(conn)
    save_assisted_review(conn, match_id, AI, "still_unclear", evidence="s4 generic", reason="generic_statement")
    save_assisted_review(conn, match_id, "Venu", "identified", evidence="s9 gives the result")
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified" and row["reviewer"] == "Venu" and row["corrected_status"] == "identified"
    assert row["ai_decision"] == "accept" and row["ai_reason"] == "generic_statement"


def test_an_ai_review_after_a_human_review_does_not_change_the_state(conn):
    match_id = unclear(conn)
    save_assisted_review(conn, match_id, "Venu", "still_unclear", evidence="s4 generic", reason="generic_statement")
    save_assisted_review(conn, match_id, AI, "identified", evidence="s9")
    row = load_queue(conn, NCT)[0]
    assert row["state"] == "human_verified" and row["reviewer"] == "Venu" and row["decision"] == "accept"


def test_a_correction_is_accepted_only_by_the_same_kind_of_reviewer(conn):
    match_id = unclear(conn)
    save_review(conn, match_id, AI, "correct", "AI proposes", "identified")
    with pytest.raises(ValueError, match="pending correction"):
        save_review(conn, match_id, "Venu", "accept_correction")                  # a human cannot accept the AI's proposal
    save_review(conn, match_id, AI, "accept_correction")
    assert state_of(conn) == "needs_review"                                       # still not human-verified


def test_the_ai_review_is_exported_beside_the_human_one_and_never_as_final(conn):
    from trialtrace.review.export_findings import export_rows
    match_id = unclear(conn)
    save_assisted_review(conn, match_id, AI, "not_identified", evidence="s3")
    [row] = export_rows(load_queue(conn, NCT))
    assert row["final_status"] == "" and row["review_state"] == "Needs review" and row["reviewer"] == ""
    assert row["ai_reviewer"] == AI and row["ai_corrected_status"] == "not_identified"
