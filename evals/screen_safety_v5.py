"""Code-only coverage screen for the v5 draw. No model calls. Writes evals/safety_v5_acquisition/screen.json."""
import json
import re
from pathlib import Path

import psycopg

from evals.prepare_safety_v2 import pool_from_database
from trialtrace.safety.safety_baseline import read_safety_statements
from trialtrace.safety.safety_compare import find_event_rows
from trialtrace.config import Settings
from trialtrace.parse.pubmed_parser import Paper

FOLDER = Path("evals/safety_v5_acquisition")
DURATION = re.compile(r"\b(?:\d+|one|two|three|six|twelve)[- ](?:weeks?|months?|years?|days?)\b|\bday \d+\b|\bweek \d+\b", re.IGNORECASE)


def screen_trial(nct_id, data):
    numeric, window, reasons = 0, 0, []
    for paper in data["selected"]:
        reading = read_safety_statements(Paper(pmid=paper["pmid"], title=paper["title"], abstract=paper["abstract"]))
        for statement in reading.statements:
            if not statement.numbers:
                continue
            if statement.kind == "event" and statement.term and find_event_rows(statement.term, data["safety"].events):
                numeric += 1
            elif statement.kind in ("serious", "death"):
                numeric += 1
            if DURATION.search(statement.quote.text):
                window += 1
    return {"nct_id": nct_id, "numeric_statements": numeric, "window_statements": window,
            "numeric_candidate": numeric > 0, "window_candidate": window > 0}


def main():
    draw = json.loads((FOLDER / "draw.json").read_text(encoding="utf-8"))
    drawn = set(draw["selected"])
    with psycopg.connect(Settings().database_url) as conn:
        pool = pool_from_database(conn)[0]
    # pool_from_database excludes trials with earlier safety readings, so only fresh ones remain.
    results = [screen_trial(n, d) for n, d in sorted(pool.items()) if n in drawn]
    summary = {"drawn": len(drawn), "eligible": len(results),
               "numeric_candidates": sum(r["numeric_candidate"] for r in results),
               "window_candidates": sum(r["window_candidate"] for r in results)}
    (FOLDER / "screen.json").write_text(json.dumps({"summary": summary, "trials": results}, indent=2), encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
