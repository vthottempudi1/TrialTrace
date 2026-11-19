"""Create fresh blank reference sheets from the recorded external draw.

Reuse the production selector and v2 packet format, never the v2 cases/labels.
"""
import json
from pathlib import Path

import psycopg

from evals import prepare_safety_v2 as packet
from trialtrace.config import Settings

FOLDER = Path("evals/safety_v3")


def main():
    draw = json.loads(Path("evals/safety_v3_acquisition/draw.json").read_text(encoding="utf-8"))
    allowed = set(draw["selected"])
    original_pool = packet.pool_from_database

    def fresh_pool(conn):
        pool, pmids, texts = original_pool(conn)
        return {n: d for n, d in pool.items() if n in allowed}, pmids, texts

    # FOLDER also controls which prior sheets count as exposure. In particular,
    # the whole v2 packet is now excluded, even though its calls bypassed the DB.
    packet.FOLDER = FOLDER
    packet.pool_from_database = fresh_pool
    packet.RULE = packet.RULE.replace("Safety v2", "Safety v3") + """

Only trials in ../safety_v3_acquisition/draw.json are eligible. The acquisition
rule predates reading new abstracts. Prior v2 cases are development data and
are excluded. Time-to-event estimates are eligible extractions but carry a
different measure from raw proportions and cannot receive numeric agreement
against the registry adverse-event counts. Multi-trial findings need explicit
local attribution; pooled and unassigned results do not support this trial.
Score these challenging subsets separately from the full naturalistic cohort.
Missing real numeric branches remain untested, even if unit tests cover them.
"""
    original_write = packet.write_trial

    def write_with_measures(folder, nct_id, data, replace_sources=False):
        count = original_write(folder, nct_id, data, replace_sources)
        (folder / "measure_labels.jsonl").write_text(
            '# One object per labels.txt statement: {"label_index":1,"measure":"participant_proportion"}\n'
            '# Use time_to_event for a reported time-to-event estimate. Do not infer from registry counts.\n',
            encoding="utf-8")
        return count

    packet.write_trial = write_with_measures
    with psycopg.connect(Settings().database_url) as conn:
        result = packet.prepare(conn, FOLDER)
    protocol = Path("evals/safety_v2/protocol.md").read_text(encoding="utf-8")
    protocol = protocol.split("## Production-selection amendment")[0]
    protocol = protocol.replace("Safety v2", "Safety v3").replace("safety_v2", "safety_v3").replace("evals.run_safety_v2", "evals.run_safety_v3")
    protocol = protocol.replace("Status: prepared for independent human labeling; NOT frozen; NO evaluation model called.", "Status: prepared for independent human labeling; NOT frozen; NO evaluation model called. See manifest.json for actual draw counts.")
    protocol = protocol.replace(
        "Read `RULE.md` for the prewritten sampling and extension rule. Amended selection: the same 30 trials, 127 abstract/trial cases, using the production first-10 paper rule.\nThe stricter pool has 48 trials and 285 clean listed abstracts; other project case sheets are excluded too.",
        f"Read RULE.md. Fresh pool: {result['pool_trials']} trials; selected {result['selected_trials']} trials / {result['selected_abstracts']} abstracts, using the production first-ten rule.")
    protocol += """

## New measure and attribution rules

Explicitly reported cumulative-incidence/probability and Kaplan-Meier safety
percentages are eligible estimates; approximate numbers, bounds and ranges are
not. Do not treat a time-to-event estimate as an ordinary participant proportion.
Label these safety_not_comparable against registry adverse-event counts.
For papers covering several trials, label only findings explicitly attributable
to the target trial. Keep ambiguous or pooled results out of trial-specific gold.
Record borderline decisions in label_notes.md before any evaluation call.

Fill measure_labels.jsonl with one object per statement, in file order:
{"label_index":1,"measure":"participant_proportion"} or measure time_to_event.
The v3 scorer separately reports measure accuracy and estimate extraction.
Attribution is evaluated through trial-specific extraction eligibility; it is not
a separate validated semantic field. Record supporting attribution evidence in
label_notes.md, especially for multi-trial papers.
"""
    (FOLDER / "protocol.md").write_text(protocol, encoding="utf-8")
    (FOLDER / "START_HERE.md").write_text(
        "# Safety v3: fresh references needed\n\n"
        f"Prepared {result['selected_trials']} trials / {result['selected_abstracts']} abstracts. "
        "No model called; no labels invented.\n\n"
        "Read RULE.md and protocol.md, then sheet.md. Fill each trial's labels.txt "
        "and match_labels.jsonl plus measure_labels.jsonl, including none for empty abstracts. "
        "Record Venu's actual review in reference_review.json only after review.\n\n"
        "Validate: `python -m evals.run_safety_v3 --check-labels`. "
        "Apply the count-based extension rule before freezing. "
        "This packet is not frozen and must not be model-read before review.\n",
        encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("pool_trials", "selected_trials", "selected_abstracts")}))


if __name__ == "__main__":
    main()
