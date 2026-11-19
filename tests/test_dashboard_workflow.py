"""Exercise real dashboard buttons against the isolated test database."""
import csv
import io
from pathlib import Path
from uuid import uuid4

import psycopg
from streamlit.testing.v1 import AppTest

from tests.test_match_review import add_finding
from trialtrace.review.export_findings import export_rows, to_csv, to_markdown
from trialtrace.review.match_review import load_queue


def test_reviews_persist_after_dashboard_reload_and_export(database_url, monkeypatch):
    monkeypatch.setenv("TRIALTRACE_DATABASE_URL", database_url)
    monkeypatch.setenv("TRIALTRACE_DISABLE_LOGIN", "true")
    run = "dashboard-check-" + uuid4().hex
    ids = []
    with psycopg.connect(database_url) as conn:
        for position, status in enumerate(("identified", "identified", "ambiguous")):
            ids.append(add_finding(conn, run_id=run, status=status, position=position,
                                   input_hash=f"{run}-{position}"))
    try:
        path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
        app = AppTest.from_file(str(path), default_timeout=60).run()
        app.sidebar.text_input[0].set_value("Dashboard workflow test").run()
        assert not app.exception

        app.button(key=f"acc_all_{ids[0]}").click().run()
        app.text_input(key=f"note_all_{ids[1]}").set_value("Synthetic correction for workflow verification")
        app.selectbox(key=f"cs_all_{ids[1]}").select("not_identified")
        app.button(key=f"corr_all_{ids[1]}").click().run()
        assert not app.exception
        app.button(key=f"acc_corr_all_{ids[1]}").click().run()
        app.text_input(key=f"note_all_{ids[2]}").set_value("Synthetic abstract cannot settle this outcome")
        app.selectbox(key=f"reason_all_{ids[2]}").select("abstract_too_short")
        app.button(key=f"unable_all_{ids[2]}").click().run()
        assert not app.exception

        # A new dashboard session and connection must see committed decisions.
        fresh = AppTest.from_file(str(path), default_timeout=60).run()
        assert not fresh.exception
        with psycopg.connect(database_url) as conn:
            queue = [row for row in load_queue(conn) if row["match_id"] in ids]
        assert len(queue) == 3
        assert [row["state"] for row in queue] == ["human_verified", "human_verified", "unresolved"]
        assert [row["status"] for row in queue] == ["identified", "identified", "ambiguous"]
        rows = export_rows(queue)
        parsed = list(csv.DictReader(io.StringIO(to_csv(rows))))
        assert [row["final_status"] for row in parsed] == ["identified", "not_identified", ""]
        assert parsed[2]["unresolved_reason"] == "abstract_too_short"
        report = to_markdown(rows, "Synthetic dashboard check")
        assert "Human-verified 2" in report and "Unresolved 1" in report
        assert "Final (human-verified): not_identified" in report
        assert "Synthetic abstract cannot settle this outcome" in report
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute("DELETE FROM paper_match_reviews WHERE match_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM paper_matches WHERE match_id = ANY(%s)", (ids,))
