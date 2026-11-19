##############################################################################
# tests/test_read_safety.py
#
# PURPOSE: Prove the safety reading step (S6): for a stored trial with registry safety data, each
#          chosen paper gets TWO stored readings (baseline and model), kept separate and insert-only;
#          rejected statements are stored with their reason; a failed model answer keeps its reason and
#          is retried, never reused; a stored answer is checked again instead of calling the model
#          again; trials with nothing to read are reported, not guessed.
#          A fake model is used: no network, no key. Uses the test database (rolled back).
##############################################################################

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.safety.safety_llm import safety_prompt_hash
from trialtrace.config import Settings
from trialtrace.contracts import SafetyArm, SafetyExtraction, Snapshot
from trialtrace.db.repository import get_safety_readings, save_papers, save_snapshot
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.safety.read_safety import read_safety_for_trial

NCT = "NCT90000098"
HASH = "a" * 64
RAW = "b" * 64
WHEN = datetime(2026, 11, 10, tzinfo=timezone.utc)
DEATHS = "There were 6 deaths in the drug group and 4 in the placebo group."
ABSTRACT = f"We enrolled 201 patients. {DEATHS} Nausea occurred in 21% of patients."


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


class FakeClient:
    """A fake OpenAI client that always replies with the same text."""

    def __init__(self, reply, model="gpt-test"):
        self.calls = []
        message = SimpleNamespace(content=reply)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model, usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def reply(*statements, document="trial_results"):
    return json.dumps({"document": document, "statements": list(statements)})


GOOD = {"type": "death", "arm": "drug group", "count": 6, "quote": DEATHS}
BAD = {"type": "death", "arm": "placebo group", "count": 99, "quote": DEATHS}          # 99 is not written


def store_trial(conn, pmids=("111",), safety="ok", abstract=ABSTRACT):
    snapshot = Snapshot(nct_id=NCT, title="T", overall_status="COMPLETED", content_hash=HASH,
                        source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="x", retrieved_at=WHEN)
    extraction = None
    if safety == "ok":
        arm = SafetyArm(nct_id=NCT, content_hash=HASH, position=0, group_id="EG000", title="Drug", deaths_affected=6)
        extraction = SafetyExtraction(status="ok", arms=[arm])
    if safety == "absent":
        extraction = SafetyExtraction(status="absent")
    save_snapshot(conn, snapshot, [], safety=extraction)
    papers = [Paper(pmid=pmid, title=f"Paper {pmid}", abstract=abstract, listed_nct_ids=[NCT]) for pmid in pmids]
    if papers:
        save_papers(conn, papers, [check_paper_link(p, NCT) for p in papers], RAW, "x.xml", "https://example.org/efetch", WHEN)


def run(conn, settings, client, run_id="safety-1", **options):
    return read_safety_for_trial(conn, settings, NCT, run_id, client=client, sleep=lambda seconds: None, **options)


def test_each_paper_gets_a_baseline_and_a_model_reading_kept_apart(conn, settings):
    store_trial(conn)
    summary = run(conn, settings, FakeClient(reply(GOOD)))
    assert (summary.status, summary.papers_read, summary.llm_calls, summary.model_statements) == ("done", 1, 1, 1)
    readings = get_safety_readings(conn, NCT)
    assert [r["system"] for r in readings] == ["baseline", "llm"]
    baseline, model = readings
    assert baseline["model"] is None and [s["kind"] for s in baseline["statements"]] == ["death", "event"]
    assert baseline["statements"][0]["numbers"] == ["6", "4"]
    assert model["status"] == "ok" and model["document"] == "trial_results"
    statement = model["statements"][0]
    assert (statement["kind"], statement["arm"], statement["affected"], statement["rejected_code"]) == ("death", "drug group", 6, None)
    assert ABSTRACT[statement["quote_start"]:statement["quote_end"]] == DEATHS == statement["quote_text"]


def test_the_model_reading_records_its_configuration_and_raw_answer(conn, settings):
    store_trial(conn)
    run(conn, settings, FakeClient(reply(GOOD)))
    model = get_safety_readings(conn, NCT)[1]
    assert model["model"] == "gpt-6.1-sol" and model["served_model"] == "gpt-test"
    assert model["prompt_hash"] == safety_prompt_hash() and model["schema_version"] == "safety-read-v8"
    assert json.loads(model["raw_response"])["statements"][0]["count"] == 6
    assert (model["input_tokens"], model["output_tokens"]) == (100, 20) and model["input_hash"] and model["reused_from"] is None


def test_a_rejected_statement_is_stored_with_its_reason(conn, settings):
    store_trial(conn)
    summary = run(conn, settings, FakeClient(reply(GOOD, BAD)))
    assert (summary.model_statements, summary.model_rejected) == (1, 1)
    statements = get_safety_readings(conn, NCT)[1]["statements"]
    assert [s["rejected_code"] for s in statements] == [None, "count_not_in_quote"]


def test_a_failed_model_answer_keeps_its_reason_and_the_baseline_is_still_stored(conn, settings):
    store_trial(conn)
    summary = run(conn, settings, FakeClient("not json"))
    assert summary.llm_failed == 1 and summary.model_statements == 0
    baseline, model = get_safety_readings(conn, NCT)
    assert (model["status"], model["error_code"], model["raw_response"]) == ("failed", "bad_json", "not json")
    assert baseline["status"] == "ok" and len(baseline["statements"]) == 2


def test_a_second_run_reuses_the_stored_answer_and_points_to_the_real_call(conn, settings):
    store_trial(conn)
    run(conn, settings, FakeClient(reply(GOOD)), "safety-1")
    first = get_safety_readings(conn, NCT, "safety-1")[1]
    client = FakeClient(reply(BAD))                                  # would answer differently, but must not be asked
    summary = run(conn, settings, client, "safety-2")
    assert (summary.llm_calls, summary.llm_reused) == (0, 1) and client.calls == []
    second = get_safety_readings(conn, NCT, "safety-2")[1]
    assert second["reused_from"] == first["reading_id"] and second["raw_response"] == first["raw_response"]
    assert second["input_tokens"] is None and second["statements"][0]["affected"] == 6
    run(conn, settings, client, "safety-3")
    assert get_safety_readings(conn, NCT, "safety-3")[1]["reused_from"] == first["reading_id"]     # never a copy of a copy
    assert len(get_safety_readings(conn, NCT, "safety-1")) == 2                                     # old rows are kept


def test_refresh_asks_the_model_again(conn, settings):
    store_trial(conn)
    run(conn, settings, FakeClient(reply(GOOD)), "safety-1")
    summary = run(conn, settings, FakeClient(reply(GOOD)), "safety-2", refresh=True)
    assert (summary.llm_calls, summary.llm_reused) == (1, 0)


def test_a_failed_answer_is_retried_not_reused(conn, settings):
    store_trial(conn)
    run(conn, settings, FakeClient("not json"), "safety-1")
    summary = run(conn, settings, FakeClient(reply(GOOD)), "safety-2")
    assert (summary.llm_calls, summary.llm_reused, summary.model_statements) == (1, 0, 1)


def test_an_answer_from_another_served_model_is_not_reused(conn, settings):
    store_trial(conn)
    run(conn, settings, FakeClient(reply(GOOD), model="gpt-old"), "safety-1")
    summary = run(conn, settings, FakeClient(reply(GOOD), model="gpt-new"), "safety-2", served_model="gpt-new")
    assert (summary.llm_calls, summary.llm_reused) == (1, 0)
    summary = run(conn, settings, FakeClient(reply(GOOD), model="gpt-new"), "safety-3", served_model="gpt-new")
    assert (summary.llm_calls, summary.llm_reused) == (0, 1)


def test_two_papers_are_read_separately_lowest_pmid_first(conn, settings):
    store_trial(conn, pmids=("222", "111"))
    summary = run(conn, settings, FakeClient(reply(GOOD)))
    assert summary.papers_read == 2 and summary.llm_calls == 1 and summary.llm_reused == 1      # the same abstract text: asked once
    assert [(r["pmid"], r["system"]) for r in get_safety_readings(conn, NCT)] == [
        ("111", "baseline"), ("111", "llm"), ("222", "baseline"), ("222", "llm")]


def test_papers_beyond_the_limit_are_named_not_read(conn, settings):
    store_trial(conn, pmids=("111", "222", "333"))
    limited = settings.model_copy(update={"paper_reader_max_papers": 2})
    summary = run(conn, limited, FakeClient(reply(GOOD)))
    assert (summary.papers_read, summary.papers_skipped, summary.skipped_pmids) == (2, 1, ["333"])


@pytest.mark.parametrize("what, status", [
    ("nothing_stored", "no_snapshot"), ("never_extracted", "no_safety_data"), ("absent", "no_safety_data"), ("no_papers", "no_papers"),
])
def test_nothing_to_read_is_reported_and_costs_no_call(conn, settings, what, status):
    if what == "never_extracted":
        store_trial(conn, safety=None)
    if what == "absent":
        store_trial(conn, safety="absent")
    if what == "no_papers":
        store_trial(conn, pmids=())
    client = FakeClient(reply(GOOD))
    summary = run(conn, settings, client)
    assert summary.status == status and client.calls == [] and get_safety_readings(conn, NCT) == []


def test_baseline_is_optional_for_safety_reading(conn, settings):
    store_trial(conn)
    summary = run(conn, settings, FakeClient(reply(GOOD)), include_baseline=False)
    readings = get_safety_readings(conn, NCT)
    assert {reading["system"] for reading in readings} == {"llm"}
    assert summary.baseline_statements == 0 and summary.llm_calls == 1
