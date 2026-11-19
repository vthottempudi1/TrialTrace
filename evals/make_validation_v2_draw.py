##############################################################################
# evals/make_validation_v2_draw.py
#
# PURPOSE: Draw the fresh trials for validation v2 (rule: evals/validation_v2/RULE.md, written before this ran).
#          Pool: completed ClinicalTrials.gov studies with at least 2 references of type RESULT and 2 to 7 listed
#          outcomes, from the first 12 pages of 1000, never used in any earlier evaluation case file and never
#          stored in our database. Fixed seed. Writes evals/validation_v2/draw.txt and pool_summary.txt.
#          Refuses to overwrite. Makes no model call.
#
# USE:     python -m evals.make_validation_v2_draw
##############################################################################

import random
from pathlib import Path

import httpx
import psycopg

from evals import make_final_draw
from trialtrace.config import Settings

SEED = 20261116
PAGES = 12
DRAW_SIZE = 400
OUT_DIR = Path(__file__).parent / "validation_v2"


def stored_trials() -> set[str]:
    with psycopg.connect(Settings().database_url) as conn:
        return {row[0] for row in conn.execute("SELECT DISTINCT nct_id FROM observations")}


def main() -> None:
    draw_file = OUT_DIR / "draw.txt"
    if draw_file.exists():
        raise SystemExit(f"{draw_file} already exists; the draw is fixed.")
    make_final_draw.PAGES = PAGES
    used = make_final_draw.used_trials()
    stored = stored_trials()
    pool = make_final_draw.fetch_pool()
    candidates = sorted(s["protocolSection"]["identificationModule"]["nctId"] for s in pool if make_final_draw.eligible(s))
    fresh = [nct for nct in candidates if nct not in used and nct not in stored]
    random.Random(SEED).shuffle(fresh)
    drawn = fresh[:DRAW_SIZE]
    OUT_DIR.mkdir(exist_ok=True)
    draw_file.write_text("\n".join(drawn) + "\n", encoding="utf-8")
    (OUT_DIR / "pool_summary.txt").write_text(
        f"seed {SEED}; pages read {PAGES} x {make_final_draw.PAGE_SIZE}; studies read {len(pool)}; eligible {len(candidates)}; "
        f"excluded as used in case files {len(set(candidates) & used)}; excluded as already stored {len(set(candidates) & stored)}; "
        f"fresh {len(fresh)}; drawn {len(drawn)}\n", encoding="utf-8")
    print((OUT_DIR / "pool_summary.txt").read_text())


if __name__ == "__main__":
    main()
