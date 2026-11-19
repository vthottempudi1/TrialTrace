##############################################################################
# tests/test_api.py
#
# PURPOSE: Prove the API returns what is stored, keeps model answers and reviews apart, refuses bad input with a clear
#          message, hides internal errors, and never calls the model. Uses the test database inside a transaction
#          that is rolled back; the API's commit is switched off for the tests.
##############################################################################

import pytest
from fastapi.testclient import TestClient

from tests.test_match_papers import NCT, FakeClient, reply, run_counted, store_trial
from trialtrace.api import create_app
from trialtrace.config import Settings


class NoCommit:
    """The test connection, with commit and rollback switched off so the fixture's rollback stays in charge."""

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        pass

    def rollback(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


@pytest.fixture
def client(conn):
    app = create_app("postgresql://unused")
    app.dependency_overrides[app.state.get_conn] = lambda: NoCommit(conn)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def stored(conn, settings):
    store_trial(conn, outcomes=("Overall Survival (OS)", "Safety"))
    run_counted(conn, settings, FakeClient(reply()), "match-1")


def first_llm_match_id(conn):
    return conn.execute("SELECT match_id FROM paper_matches WHERE nct_id = %s AND system = 'llm' ORDER BY match_id LIMIT 1", (NCT,)).fetchone()[0]


def test_health_checks_the_database(client):
    assert client.get("/health").json() == {"status": "ok", "database": True}


def test_trial_lists_outcomes_papers_findings_and_counts(client, stored):
    body = client.get(f"/trials/{NCT}").json()
    assert body["nct_id"] == NCT and len(body["outcomes"]) == 2 and len(body["papers"]) == 1
    assert body["counts"]["needs_review"] == 2 and len(body["findings"]) == 2
    assert {f["review_state"] for f in body["findings"]} == {"Needs review"} and all(f["final_status"] == "" for f in body["findings"])
    assert "not clinical risk" in body["note"]


def test_trials_summary_counts_the_findings(client, stored):
    body = client.get("/trials").json()
    mine = [t for t in body["trials"] if t["nct_id"] == NCT][0]
    assert mine["needs_review"] == 2 and mine["total"] == 2


def test_bad_or_unknown_trial_ids_are_clear_errors(client, stored):
    assert client.get("/trials/abc").status_code == 422
    assert client.get("/trials/NCT00000001").status_code == 404
    assert client.get("/trials/NCT00000001/findings.csv").status_code == 404


def test_csv_export_has_one_line_per_finding(client, stored):
    response = client.get(f"/trials/{NCT}/findings.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert len(response.text.strip().splitlines()) == 1 + 2


def test_estimate_is_free_and_matches_the_stored_state(client, stored):
    body = client.get(f"/trials/{NCT}/estimate").json()
    assert body["status"] == "ok" and body["new_calls"] == 0 and body["reused"] == 2
    assert client.get(f"/trials/{NCT}/estimate", params={"refresh": "true"}).json()["new_calls"] == 2


def test_a_review_is_saved_apart_from_the_model_answer_and_shows_in_the_trial(client, stored, conn):
    match_id = first_llm_match_id(conn)
    response = client.post(f"/findings/{match_id}/review", json={"reviewer": "Venu", "decision": "accept", "note": "ok"})
    assert response.status_code == 201
    body = client.get(f"/trials/{NCT}").json()
    reviewed = [f for f in body["findings"] if f["reviewer"] == "Venu"][0]
    assert reviewed["review_state"] == "Human-verified" and reviewed["final_status"] == "identified" and reviewed["model_status"] == "identified"
    assert body["counts"] == {"needs_review": 1, "human_verified": 1, "unresolved": 0}


def test_review_rules_are_422_and_an_unknown_finding_is_404(client, stored, conn):
    match_id = first_llm_match_id(conn)
    assert client.post(f"/findings/{match_id}/review", json={"reviewer": " ", "decision": "accept"}).status_code == 422
    bad = client.post(f"/findings/{match_id}/review", json={"reviewer": "Venu", "decision": "undetermined", "note": "why"})
    assert bad.status_code == 422 and "reason" in bad.json()["detail"]
    assert client.post("/findings/999999999/review", json={"reviewer": "Venu", "decision": "accept"}).status_code == 404
    assert client.post(f"/findings/{match_id}/review", json={"decision": "accept"}).status_code == 422          # reviewer missing


def test_unexpected_errors_are_logged_and_hidden(conn, caplog):
    app = create_app("postgresql://unused")

    class Broken:
        def execute(self, *args, **kwargs):
            raise RuntimeError("secret internal detail")

    app.dependency_overrides[app.state.get_conn] = lambda: Broken()
    response = TestClient(app, raise_server_exceptions=False).get("/health")
    assert response.status_code == 500 and response.json()["error"] == "internal error" and "secret" not in response.text
    assert any("secret internal detail" in (record.exc_text or "") for record in caplog.records)


def test_review_options_list_the_reasons(client):
    body = client.get("/review-options").json()
    assert "generic_statement" in body["unresolved_reasons"] and "undetermined" in body["decisions"]


def test_the_dashboard_can_find_the_package_when_started_with_streamlit_run():
    """`streamlit run dashboard/app.py` puts only dashboard/ on the import path; the page adds the project folder first.
    (A real browser check found this missing; a test inside pytest cannot see it, so the order is checked in the file.)"""
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert text.index("sys.path.insert(0") < text.index("from trialtrace.")
