##############################################################################
# tests/test_match_papers.py
#
# PURPOSE: Prove the paper-matching pipeline: the frozen reader settings, how the two systems'
#          answers are compared and prioritised for review, that BOTH predictions are stored as
#          separate insert-only rows with the model and prompt recorded, that a failed LLM
#          answer keeps its reason, and that match_trial reads the stored trial, papers and
#          outcomes and respects the paper cap. A fake model is used: no network, no key.
##############################################################################

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.config import Settings
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import get_paper_matches, get_skipped_papers, save_paper_matches, save_papers, save_snapshot
from trialtrace.match_papers import choose_papers, compare_predictions, estimate_match_calls, match_pair, match_trial, rows_for_pair
from trialtrace.paper_reader import probe_served_model, prompt_hash, reader_info, reader_settings
from trialtrace.parse.pubmed_parser import Paper

NCT = "NCT90000099"
HASH = "a" * 64
RAW = "b" * 64
ABSTRACT = "We enrolled 50 patients. The primary end point was overall survival (OS). Safety was assessed."
QUOTE = "The primary end point was overall survival (OS)."
WHEN = datetime(2026, 11, 6, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


class FakeClient:
    """A fake OpenAI client that always replies with the same text."""

    def __init__(self, reply):
        self.calls = []
        message = SimpleNamespace(content=reply)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def reply(status="identified", quote=QUOTE, name="overall survival", role="primary", document="trial_results"):
    return json.dumps({"document": document, "status": status, "quote": quote, "name": name, "role": role})


def outcome(position=0, text="Overall Survival (OS)"):
    return RegisteredOutcome(nct_id=NCT, content_hash=HASH, role="primary", position=position, measure_text=text, time_frame="5 years")


def paper(pmid="111", abstract=ABSTRACT):
    return Paper(pmid=pmid, title=f"Paper {pmid}", abstract=abstract, listed_nct_ids=[NCT])


def pair_with(reply_text, settings, the_paper=None):
    reader = reader_settings(settings)
    return match_pair(outcome(), the_paper or paper(), RAW, reader, client=FakeClient(reply_text), sleep=lambda seconds: None)


# --- the frozen reader settings -----------------------------------------------------------

def test_the_reader_uses_the_frozen_model_and_leaves_the_callers_settings_alone(settings):
    reader = reader_settings(settings)
    assert reader.model_name == "gpt-6.1-sol" and reader.llm_temperature is None
    assert settings.model_name != "gpt-6.1-sol" or settings.llm_temperature == 0      # the original was not changed


def test_reader_info_names_the_model_prompt_and_schema(settings):
    info = reader_info(reader_settings(settings))
    assert info == {"model": "gpt-6.1-sol", "temperature": None, "prompt_hash": prompt_hash(), "schema_version": "paper-match-v2"}
    assert len(prompt_hash()) == 12


# --- comparing the two systems -------------------------------------------------------------

@pytest.mark.parametrize("baseline,llm,error,agreement,risk", [
    ("identified", "identified", None, "agree_identified", "low"),
    ("not_identified", "not_identified", None, "agree_not_identified", "low"),
    ("not_identified", "identified", None, "llm_only", "high"),
    ("ambiguous", "identified", None, "llm_only", "high"),
    ("identified", "not_identified", None, "baseline_only", "high"),
    ("identified", "ambiguous", None, "llm_unclear", "high"),
    ("not_identified", "ambiguous", None, "llm_unclear", "high"),
    ("ambiguous", "not_identified", None, "different", "medium"),
    ("identified", "failed", "AuthenticationError", "llm_failed", "high"),
    ("not_identified", "failed", "quote_not_found", "llm_evidence_invalid", "high"),
    ("not_identified", "failed", "name_not_in_quote", "llm_evidence_invalid", "high"),
    ("no_text", "no_text", None, "no_text", "low"),
])
def test_the_agreement_and_review_priority(baseline, llm, error, agreement, risk):
    got_agreement, got_risk, why = compare_predictions(baseline, llm, error)
    assert (got_agreement, got_risk) == (agreement, risk) and why


def test_a_failed_llm_says_the_baseline_is_only_a_provisional_fallback():
    _, _, why = compare_predictions("identified", "failed", "TimeoutError")
    assert "provisional fallback" in why


# --- one pair, both systems ------------------------------------------------------------------

def test_both_systems_agree_when_the_abstract_names_the_outcome(settings):
    result = pair_with(reply(), settings)
    assert result.baseline.status == "identified" and result.llm.status == "identified"
    assert result.agreement == "agree_identified" and result.risk == "low"


def test_the_llm_failure_keeps_its_reason_and_the_baseline_answer_stays_separate(settings):
    result = pair_with(reply(quote="invented sentence"), settings)
    assert result.llm.status == "failed" and result.llm.error_code == "quote_not_found"
    assert result.baseline.status == "identified"                # the baseline answer is untouched
    assert result.agreement == "llm_evidence_invalid" and result.risk == "high"


def test_a_protocol_is_not_identified_and_is_not_a_disagreement_about_evidence(settings):
    result = pair_with(reply(document="protocol"), settings)
    assert result.llm.status == "not_identified" and result.llm.reason == "paper is a protocol"
    assert result.agreement == "baseline_only" and result.risk == "high"       # the baseline found the name; a person should look


# --- the two stored rows ------------------------------------------------------------------------

def test_two_separate_rows_are_made_with_the_model_and_prompt_on_the_llm_row_only(settings):
    result = pair_with(reply(), settings)
    info = reader_info(reader_settings(settings))
    baseline_row, llm_row = rows_for_pair("match-1", HASH, result, info)
    assert (baseline_row["system"], llm_row["system"]) == ("baseline", "llm")
    assert "model" not in baseline_row and "prompt_hash" not in baseline_row
    assert llm_row["model"] == "gpt-6.1-sol" and llm_row["prompt_hash"] == prompt_hash()
    assert llm_row["raw_response"] == reply() and llm_row["input_tokens"] == 100
    assert llm_row["quote_text"] == QUOTE and llm_row["quote_start"] == ABSTRACT.index(QUOTE)


def test_a_failed_llm_row_keeps_the_error_code_and_the_raw_answer(settings):
    result = pair_with(reply(quote="invented sentence"), settings)
    _, llm_row = rows_for_pair("match-1", HASH, result, reader_info(reader_settings(settings)))
    assert llm_row["status"] == "failed" and llm_row["error_code"] == "quote_not_found"
    assert "invented sentence" in llm_row["raw_response"] and llm_row.get("quote_text") is None


# --- the database -----------------------------------------------------------------------------------

def stored_snapshot():
    return Snapshot(nct_id=NCT, title="T", sponsor="Acme", overall_status="COMPLETED", content_hash=HASH,
                    registry_last_update="2025-11-21", has_results=True, source_url=f"https://clinicaltrials.gov/study/{NCT}",
                    raw_file_path="x", retrieved_at=WHEN)


def store_trial(conn, pmids=("111",), outcomes=("Overall Survival (OS)", "Safety")):
    extraction = OutcomeExtraction(status="ok", outcomes=[outcome(i, text) for i, text in enumerate(outcomes)])
    save_snapshot(conn, stored_snapshot(), [], extraction)
    papers = [paper(pmid) for pmid in pmids]
    links = [check_paper_link(p, NCT) for p in papers]
    save_papers(conn, papers, links, RAW, "data/raw/x.xml", "https://example.org/efetch", WHEN)


def test_match_trial_stores_both_predictions_for_every_outcome_and_paper(conn, settings):
    store_trial(conn)
    summary = match_trial(conn, settings, NCT, "match-1", client=FakeClient(reply()), sleep=lambda s: None)
    assert summary.status == "done" and summary.pairs == 2 and summary.papers_used == 1
    rows = get_paper_matches(conn, NCT)
    assert len(rows) == 4 and sorted({r["system"] for r in rows}) == ["baseline", "llm"]
    assert {r["match_run_id"] for r in rows} == {"match-1"} and {r["registry_hash"] for r in rows} == {HASH}


def test_each_run_adds_rows_and_old_rows_are_kept(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    match_trial(conn, settings, NCT, "match-1", client=FakeClient(reply()), sleep=lambda s: None)
    match_trial(conn, settings, NCT, "match-2", client=FakeClient(reply()), sleep=lambda s: None)
    assert len(get_paper_matches(conn, NCT, "match-1")) == 2 and len(get_paper_matches(conn, NCT, "match-2")) == 2
    assert {r["match_run_id"] for r in get_paper_matches(conn, NCT)} == {"match-2"}      # newest run by default


def test_only_the_capped_number_of_papers_is_read_and_the_rest_are_counted(conn, settings):
    store_trial(conn, pmids=("333", "111", "222", "444"), outcomes=("Overall Survival (OS)",))
    settings.paper_reader_max_papers = 2
    summary = match_trial(conn, settings, NCT, "match-1", client=FakeClient(reply()), sleep=lambda s: None)
    assert summary.papers_used == 2 and summary.papers_skipped == 2 and summary.skipped_pmids == ["333", "444"]
    assert {r["pmid"] for r in get_paper_matches(conn, NCT)} == {"111", "222"}           # lowest PMIDs first
    assert [p["pmid"] for p in get_skipped_papers(conn, NCT, "match-1")] == ["333", "444"]   # the gap is on record


def test_papers_without_an_abstract_or_a_listed_link_are_not_read(conn, settings):
    papers = [paper("111", abstract=None), paper("222")]
    save_snapshot(conn, stored_snapshot(), [], OutcomeExtraction(status="ok", outcomes=[outcome()]))
    save_papers(conn, papers, [check_paper_link(p, NCT) for p in papers], RAW, "x", "https://example.org", WHEN)
    chosen, skipped = choose_papers(conn, NCT, 3)
    assert [c["pmid"] for c in chosen] == ["222"] and skipped == []


@pytest.mark.parametrize("what,status", [("nothing_stored", "no_snapshot"), ("no_papers", "no_papers"), ("no_outcomes", "no_outcomes")])
def test_nothing_to_match_is_reported_not_guessed(conn, settings, what, status):
    if what == "no_papers":
        save_snapshot(conn, stored_snapshot(), [], OutcomeExtraction(status="ok", outcomes=[outcome()]))
    if what == "no_outcomes":
        save_snapshot(conn, stored_snapshot(), [], OutcomeExtraction(status="ok", outcomes=[]))
    client = FakeClient(reply())
    summary = match_trial(conn, settings, NCT, "match-1", client=client, sleep=lambda s: None)
    assert summary.status == status and client.calls == [] and get_paper_matches(conn, NCT) == []


def test_save_and_get_round_trip_keeps_nulls_as_null(conn):
    save_paper_matches(conn, [{
        "match_run_id": "match-1", "nct_id": NCT, "registry_hash": HASH, "outcome_position": 0, "outcome_role": "primary",
        "outcome_text": "OS", "pmid": "111", "paper_raw_hash": RAW, "system": "baseline", "status": "not_identified",
    }])
    [row] = get_paper_matches(conn, NCT)
    assert row["quote_text"] is None and row["model"] is None and row["status"] == "not_identified"


def test_the_same_prediction_cannot_be_stored_twice_in_one_run(conn):
    import psycopg
    row = {"match_run_id": "match-1", "nct_id": NCT, "registry_hash": HASH, "outcome_position": 0, "outcome_role": "primary",
           "outcome_text": "OS", "pmid": "111", "paper_raw_hash": RAW, "system": "llm", "status": "identified",
           "quote_text": "q", "quote_start": 0, "quote_end": 1}
    save_paper_matches(conn, [row])
    with pytest.raises(psycopg.errors.UniqueViolation):
        save_paper_matches(conn, [row])


def test_a_primary_and_a_secondary_outcome_with_the_same_position_are_different_outcomes(conn):
    base = {"match_run_id": "match-1", "nct_id": NCT, "registry_hash": HASH, "outcome_position": 0, "outcome_text": "OS",
            "pmid": "111", "paper_raw_hash": RAW, "system": "baseline", "status": "not_identified"}
    save_paper_matches(conn, [{**base, "outcome_role": "primary"}, {**base, "outcome_role": "secondary"}])
    assert len(get_paper_matches(conn, NCT)) == 2


def test_the_default_limit_is_ten_papers_per_trial(settings):
    assert settings.paper_reader_max_papers == 10


def test_nothing_is_recorded_as_skipped_when_every_paper_was_read(conn, settings):
    store_trial(conn, pmids=("111", "222"), outcomes=("Overall Survival (OS)",))
    summary = match_trial(conn, settings, NCT, "match-1", client=FakeClient(reply()), sleep=lambda s: None)
    assert summary.papers_skipped == 0 and summary.skipped_pmids == [] and get_skipped_papers(conn, NCT, "match-1") == []


# --- reusing stored answers instead of paying for the same call again ---------------------------------

def run_counted(conn, settings, client, run_id, refresh=False):
    return match_trial(conn, settings, NCT, run_id, client=client, sleep=lambda s: None, refresh=refresh)


def test_a_second_run_with_the_same_question_makes_no_model_call(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)", "Time to death"))
    first_client, second_client = FakeClient(reply()), FakeClient(reply(status="not_identified"))
    first = run_counted(conn, settings, first_client, "match-1")
    second = run_counted(conn, settings, second_client, "match-2")
    assert (first.llm_calls, first.llm_reused) == (2, 0)
    assert (second.llm_calls, second.llm_reused) == (0, 2) and second_client.calls == []


def test_the_reused_answer_is_the_stored_answer_not_whatever_a_new_call_would_say(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    run_counted(conn, settings, FakeClient(reply(status="not_identified")), "match-2")
    llm_rows = [r for r in get_paper_matches(conn, NCT, "match-2") if r["system"] == "llm"]
    assert [r["status"] for r in llm_rows] == ["identified"]


def test_a_reused_row_points_to_the_real_call_keeps_the_raw_answer_and_counts_no_tokens(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    run_counted(conn, settings, FakeClient(reply()), "match-2")
    [original] = [r for r in get_paper_matches(conn, NCT, "match-1") if r["system"] == "llm"]
    [copy] = [r for r in get_paper_matches(conn, NCT, "match-2") if r["system"] == "llm"]
    assert original["reused_from"] is None and original["input_tokens"] == 100
    assert copy["reused_from"] == original["match_id"] and copy["raw_response"] == original["raw_response"]
    assert copy["input_tokens"] is None and copy["seconds"] is None and copy["input_hash"] == original["input_hash"]


def test_a_copy_of_a_copy_still_points_to_the_original_call(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    for run_id in ("match-1", "match-2", "match-3"):
        run_counted(conn, settings, FakeClient(reply()), run_id)
    [original] = [r for r in get_paper_matches(conn, NCT, "match-1") if r["system"] == "llm"]
    [third] = [r for r in get_paper_matches(conn, NCT, "match-3") if r["system"] == "llm"]
    assert third["reused_from"] == original["match_id"]


def test_the_baseline_is_always_recomputed_and_stored_in_every_run(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    run_counted(conn, settings, FakeClient(reply()), "match-2")
    assert sorted(r["system"] for r in get_paper_matches(conn, NCT, "match-2")) == ["baseline", "llm"]


def test_refresh_asks_the_model_again(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    again = FakeClient(reply(status="not_identified"))
    summary = run_counted(conn, settings, again, "match-2", refresh=True)
    assert (summary.llm_calls, summary.llm_reused) == (1, 0) and len(again.calls) == 1
    [row] = [r for r in get_paper_matches(conn, NCT, "match-2") if r["system"] == "llm"]
    assert row["status"] == "not_identified" and row["reused_from"] is None


def test_a_failed_answer_is_retried_not_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply(quote="an invented sentence")), "match-1")       # code rejects it: failed
    good = FakeClient(reply())
    summary = run_counted(conn, settings, good, "match-2")
    assert (summary.llm_calls, summary.llm_reused) == (1, 0)
    [row] = [r for r in get_paper_matches(conn, NCT, "match-2") if r["system"] == "llm"]
    assert row["status"] == "identified"


def test_a_different_model_or_prompt_or_abstract_or_outcome_is_not_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    other_model = settings.model_copy(update={"paper_reader_model": "some-other-model"})
    assert run_counted(conn, other_model, FakeClient(reply()), "match-2").llm_calls == 1           # another model
    other_temperature = settings.model_copy(update={"paper_reader_temperature": 0.5})
    assert run_counted(conn, other_temperature, FakeClient(reply()), "match-3").llm_calls == 1     # another setting


def test_a_changed_abstract_or_outcome_text_is_not_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    changed = ABSTRACT + " One more sentence."
    from trialtrace.parse.pubmed_parser import Paper
    new_paper = Paper(pmid="111", title="Paper 111", abstract=changed, listed_nct_ids=[NCT])
    save_papers(conn, [new_paper], [check_paper_link(new_paper, NCT)], "c" * 64, "x", "https://example.org/efetch", datetime(2026, 11, 9, tzinfo=timezone.utc))
    assert run_counted(conn, settings, FakeClient(reply()), "match-2").llm_calls == 1               # new abstract text


def test_the_same_question_after_a_registry_edit_that_left_the_outcome_alone_is_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    save_snapshot(conn, Snapshot(nct_id=NCT, title="T edited", sponsor="Acme", overall_status="COMPLETED", content_hash="e" * 64,
                                 registry_last_update="2026-01-01", has_results=True, source_url=f"https://clinicaltrials.gov/study/{NCT}",
                                 raw_file_path="x", retrieved_at=datetime(2026, 11, 9, tzinfo=timezone.utc)), [],
                  OutcomeExtraction(status="ok", outcomes=[
                      RegisteredOutcome(nct_id=NCT, content_hash="e" * 64, role="primary", position=0,
                                        measure_text="Overall Survival (OS)", time_frame="5 years")]))
    summary = run_counted(conn, settings, FakeClient(reply()), "match-2")
    assert (summary.llm_calls, summary.llm_reused) == (0, 1)


# --- the served model version: old answers are not reused after the provider changes the model ---------------

def run_served(conn, settings, client, run_id, served_model):
    return match_trial(conn, settings, NCT, run_id, client=client, sleep=lambda s: None, served_model=served_model)


def test_every_real_call_records_the_model_name_the_api_reported(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    [row] = [r for r in get_paper_matches(conn, NCT) if r["system"] == "llm"]
    assert row["served_model"] == "gpt-test"                       # what the fake API reported
    assert row["model"] == "gpt-6.1-sol"                           # the configured name is kept separately


def test_a_copy_keeps_the_served_model_of_the_original_call(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    run_served(conn, settings, FakeClient(reply()), "match-2", "gpt-test")
    [copy] = [r for r in get_paper_matches(conn, NCT, "match-2") if r["system"] == "llm"]
    assert copy["reused_from"] is not None and copy["served_model"] == "gpt-test"


def test_answers_from_the_same_served_model_are_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    summary = run_served(conn, settings, FakeClient(reply()), "match-2", "gpt-test")
    assert (summary.llm_calls, summary.llm_reused) == (0, 1)


def test_answers_from_a_different_served_model_are_not_reused(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    client = FakeClient(reply(status="not_identified"))
    summary = run_served(conn, settings, client, "match-2", "gpt-test-newer-version")      # the provider moved the alias
    assert (summary.llm_calls, summary.llm_reused) == (1, 0) and len(client.calls) == 1


def test_old_rows_with_no_served_model_are_not_reused_once_the_check_is_on(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    conn.execute("UPDATE paper_matches SET served_model = NULL")           # rows made before the column existed
    assert run_served(conn, settings, FakeClient(reply()), "match-2", "gpt-test").llm_calls == 1


def test_without_a_served_model_the_old_behaviour_is_kept(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    run_counted(conn, settings, FakeClient(reply()), "match-1")
    conn.execute("UPDATE paper_matches SET served_model = NULL")
    summary = run_served(conn, settings, FakeClient(reply()), "match-2", None)      # the probe failed: reuse by fingerprint only
    assert (summary.llm_calls, summary.llm_reused) == (0, 1)


# --- the probe --------------------------------------------------------------------------------------------------

def test_the_probe_returns_the_served_model_with_one_tiny_call(settings):
    client = FakeClient("OK")
    assert probe_served_model(reader_settings(settings), client=client, sleep=lambda s: None) == "gpt-test"
    assert len(client.calls) == 1 and "single word OK" in client.calls[0]["messages"][0]["content"]


def test_the_probe_returns_none_when_the_call_fails(settings):
    assert probe_served_model(reader_settings(settings)) is None            # no API key: a failed result, not a crash



def test_llm_only_matching_never_runs_or_stores_a_baseline(conn, settings, monkeypatch):
    from trialtrace.compare.paper_outcomes import check_paper_outcomes
    from trialtrace.compare.paper_rollup import check_paper_summary
    import trialtrace.match_papers as module
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    def forbidden(*args):
        raise AssertionError("The disabled baseline must not run")
    monkeypatch.setattr(module, "match_one", forbidden)
    summary = match_trial(conn, settings, NCT, "llm-only", client=FakeClient(reply()), include_baseline=False)
    assert summary.pairs == 1 and not summary.baseline_enabled
    assert summary.agreements == {"llm_identified": 1}
    assert {row["system"] for row in get_paper_matches(conn, NCT)} == {"llm"}
    details = check_paper_outcomes(conn, NCT)
    summaries = check_paper_summary(conn, NCT)
    assert len(details) == len(summaries) == 1
    assert details[0].category == "paper_reports_outcome"
    assert summaries[0].category == "paper_summary_reported_no_detail"
    assert "Both" not in details[0].claim
    assert not any("baseline's roll-up" in text for text in summaries[0].limitations)
    assert "disputed" not in summaries[0].category


def test_llm_only_failure_remains_unknown_and_reaches_review(conn, settings):
    from trialtrace.compare.paper_outcomes import check_paper_outcomes
    from trialtrace.compare.paper_rollup import check_paper_summary
    from trialtrace.contracts import StoredFinding
    from trialtrace.review.review import needs_review, review_priority
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    match_trial(conn, settings, NCT, "llm-failed", client=FakeClient(reply(quote="invented text")), include_baseline=False)
    [detail] = check_paper_outcomes(conn, NCT)
    [summary] = check_paper_summary(conn, NCT)
    assert detail.category == "paper_check_failed" and "fallback" not in detail.claim
    assert summary.category == "unknown"
    item = StoredFinding(finding_id="test", run_id="test", finding=summary)
    assert needs_review(item) and review_priority(item) == 0


def test_llm_only_not_identified_is_not_a_system_disagreement(conn, settings):
    from trialtrace.compare.paper_outcomes import check_paper_outcomes
    from trialtrace.compare.paper_rollup import check_paper_summary
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    match_trial(conn, settings, NCT, "llm-not-found", client=FakeClient(reply(status="not_identified")), include_baseline=False)
    [detail] = check_paper_outcomes(conn, NCT)
    [summary] = check_paper_summary(conn, NCT)
    assert detail.category == "paper_outcome_not_identified"
    assert summary.category == "paper_summary_not_identified"
    assert "full paper" in detail.claim


def test_outcome_cache_key_is_specific_to_the_target_trial(settings):
    from trialtrace.paper_reader import llm_input_hash
    info = reader_info(reader_settings(settings))
    assert llm_input_hash("OS", "5 years", ABSTRACT, info, NCT) != llm_input_hash(
        "OS", "5 years", ABSTRACT, info, "NCT90000098")


def test_reusable_answer_from_another_target_is_not_selected(conn, settings):
    from trialtrace.paper_reader import llm_input_hash
    from trialtrace.db.repository import find_reusable_match
    store_trial(conn, outcomes=("Overall Survival (OS)",))
    match_trial(conn, settings, NCT, "target-1", client=FakeClient(reply()), include_baseline=False)
    [row] = get_paper_matches(conn, NCT)
    wrong_target_hash = llm_input_hash("Overall Survival (OS)", "5 years", ABSTRACT,
                                      reader_info(reader_settings(settings)), "NCT90000098")
    assert row["input_hash"] != wrong_target_hash
    assert find_reusable_match(conn, wrong_target_hash, None) is None


def test_outcome_cache_cannot_silently_omit_trial_attribution(settings):
    from trialtrace.paper_reader import llm_input_hash
    with pytest.raises(ValueError, match="target NCT ID"):
        llm_input_hash("OS", "5 years", ABSTRACT, reader_info(reader_settings(settings)), None)


# --- the estimate must equal what the run then does -----------------------------------------------------

def test_the_estimate_matches_the_real_run_and_costs_nothing(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)", "Time to death"))
    before = estimate_match_calls(conn, settings, NCT)
    assert (before.status, before.pairs, before.reused, before.new_calls) == ("ok", 2, 0, 2)
    assert get_paper_matches(conn, NCT) == []                         # estimating stored nothing
    first = run_counted(conn, settings, FakeClient(reply()), "match-1")
    assert first.llm_calls == before.new_calls
    after = estimate_match_calls(conn, settings, NCT)
    assert (after.pairs, after.reused, after.new_calls) == (2, 2, 0)
    assert estimate_match_calls(conn, settings, NCT, refresh=True).new_calls == 2     # refresh asks again


def test_the_estimate_respects_the_paper_cap_and_a_new_outcome(conn, settings):
    store_trial(conn, pmids=("111", "222", "333"), outcomes=("Overall Survival (OS)",))
    settings.paper_reader_max_papers = 2
    estimate = estimate_match_calls(conn, settings, NCT)
    assert (estimate.pairs, estimate.papers_used, estimate.papers_skipped) == (2, 2, 1)


@pytest.mark.parametrize("what,status", [("nothing_stored", "no_snapshot"), ("no_papers", "no_papers")])
def test_the_estimate_names_why_there_is_nothing_to_ask(conn, settings, what, status):
    if what == "no_papers":
        save_snapshot(conn, stored_snapshot(), [], OutcomeExtraction(status="ok", outcomes=[outcome()]))
    assert estimate_match_calls(conn, settings, NCT).status == status
