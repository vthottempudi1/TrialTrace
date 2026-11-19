##############################################################################
# evals/select_validation_v2.py
#
# PURPOSE: Apply the selection rule of evals/validation_v2/RULE.md to the drawn and fetched trials: the natural
#          set, in draw order. Reads stored registry and paper data only (no model call).
#          Writes natural_trials.txt and selection.json (with each trial's pair count).
#
# USE:     python -m evals.select_validation_v2
##############################################################################

import json
from pathlib import Path

import psycopg

from trialtrace.config import Settings
from trialtrace.match_papers import choose_papers

FOLDER = Path(__file__).parent / "validation_v2"
NATURAL_PAIRS = 300


def outcome_count(conn, nct_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM registered_outcomes o JOIN latest_snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash "
        "WHERE o.nct_id = %s", (nct_id,)).fetchone()
    return row[0]


def listed_papers_with_abstract(conn, nct_id: str) -> list[dict]:
    papers, _skipped = choose_papers(conn, nct_id, 10)         # the same papers the match step would read
    return papers


def main() -> None:
    draw = [line.strip() for line in (FOLDER / "draw.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    natural, skipped = [], {}
    natural_pairs = 0
    with psycopg.connect(Settings().database_url) as conn:
        for nct_id in draw:
            outcomes = outcome_count(conn, nct_id)
            papers = listed_papers_with_abstract(conn, nct_id)
            if len(papers) < 2 or not 2 <= outcomes <= 7 or len(papers) > 5:
                skipped[nct_id] = f"{outcomes} outcomes, {len(papers)} usable papers"
                continue
            pairs = outcomes * len(papers)
            if natural_pairs < NATURAL_PAIRS:
                natural.append({"nct_id": nct_id, "outcomes": outcomes, "papers": len(papers), "pairs": pairs})
                natural_pairs += pairs
    (FOLDER / "natural_trials.txt").write_text("\n".join(t["nct_id"] for t in natural) + "\n", encoding="utf-8")
    (FOLDER / "selection.json").write_text(json.dumps({"natural": natural, "skipped": skipped}, indent=2), encoding="utf-8")
    print(f"natural: {len(natural)} trials, {natural_pairs} pairs | not eligible: {len(skipped)}")


if __name__ == "__main__":
    main()
