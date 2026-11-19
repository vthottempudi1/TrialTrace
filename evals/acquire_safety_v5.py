"""Metadata-only draw for the v5 coverage evaluation. No models, no old-source edits."""
from pathlib import Path

from evals import acquire_safety_v3 as acquisition


def main():
    acquisition.FOLDER = Path("evals/safety_v5_acquisition")
    acquisition.SEED = 20261115
    acquisition.PARAMS = dict(acquisition.PARAMS, pageSize=1000, sort="LastUpdatePostDate:asc")
    acquisition.RULE = """# Safety v5 acquisition rule (coverage evaluation)

Written before the draw and before reading any candidate abstract.
Purpose: v2-v4 never tested real agreeing/differing numbers or real window
assertions. v5 looks for unseen trials where those branches can occur.
Query completed phase 3 trials with results, first posted by 2023-12-31, ordered
by last update ASCENDING (a different slice from earlier draws); take the first
1000 records. Keep the exact response. Remove every trial already stored (all
earlier evaluations included), sort IDs, shuffle once with seed 20261115 and
ingest the first 100. Do not redraw. Failures stay in receipts. No model calls.

Coverage screen (evals/screen_safety_v5.py, CODE ONLY, no model): for each
ingested trial with posted registry safety and a clean listed abstract, the plain-code
baseline reader lists safety statements; a trial is a "numeric candidate" if one
statement names an event, death or serious total that has a registry row, and a
"window candidate" if the abstract states a follow-up duration beside a count.
The screen output is saved BEFORE any labelling. Selection after it: all numeric
candidates and window candidates (up to 30 trials, in seeded order), plus 15 other
eligible trials drawn with the same seed so empty/other cases are still measured.
The enriched sample is reported as such: never as natural prevalence and never
pooled with v2-v4. Model output is never used for selection. Labels come from
Venu or an independent reviewer, recorded honestly; no simulated approvals.
"""
    acquisition.main()


if __name__ == "__main__":
    main()
