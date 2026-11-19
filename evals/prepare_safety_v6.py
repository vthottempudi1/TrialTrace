"""Prepare the v5 coverage-enriched reference packets from the saved screen. No models."""
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals import prepare_safety_v2 as legacy
from evals.make_safety_cases import blind_sheet
from evals.prepare_safety_v4 import lock_candidate, write_json, write_trial
from trialtrace.config import Settings

FOLDER = Path("evals/safety_v6")
ACQUISITION = Path("evals/safety_v6_acquisition")
SEED = 20261115
CANDIDATE_TRIALS = 12
OTHER_TRIALS = 8

RULE = """# Safety v6 reference selection rule (clean validation)

Written before labelling; see ../safety_v6_acquisition/RULE.md. Uses the 80 trials in the v6 draw and its saved
code-only screen (no model). Eligible = posted registry safety plus a clean abstract. Sample = the first 12 eligible
numeric or window candidates in saved draw order, plus the first 8 other eligible trials in saved draw order.
This sample is ENRICHED: report it as such, never pool it with earlier sets. Production paper selection applies.
The system is locked before labelling (system_candidate.json, ../safety_v6/system_lock.json); changing it afterwards
means the run is development, not validation. Venu labels every abstract from the abstract and registry alone;
no AI draft exists or is shown first. No model call before complete reviewed references and a hashed freeze.
Conventions: ../safety_v6/CONVENTIONS.md. Model output never selects trials.
"""


def main():
    FOLDER.mkdir(exist_ok=True)
    (FOLDER / "RULE.md").write_text(RULE, encoding="utf-8")
    protocol = Path("evals/safety_v4_protocol.md").read_text(encoding="utf-8") + chr(10) * 2 + Path("evals/safety_v6/CONVENTIONS.md").read_text(encoding="utf-8")
    (FOLDER / "protocol.md").write_text(protocol, encoding="utf-8")
    lock_candidate(FOLDER)
    screen = json.loads((ACQUISITION / "screen.json").read_text(encoding="utf-8"))["trials"]
    order = json.loads((ACQUISITION / "draw.json").read_text(encoding="utf-8"))["selected"]
    screened = {t["nct_id"]: t for t in screen}
    eligible = [n for n in order if n in screened]
    candidates = [n for n in eligible if screened[n]["numeric_candidate"] or screened[n]["window_candidate"]][:CANDIDATE_TRIALS]
    others = [n for n in eligible if n not in set(candidates) and not (screened[n]["numeric_candidate"] or screened[n]["window_candidate"])]
    selected, reserve = candidates + others[:OTHER_TRIALS], others[OTHER_TRIALS:]
    legacy.FOLDER = FOLDER
    with psycopg.connect(Settings().database_url) as conn:
        pool, excluded_pmids, excluded_texts = legacy.pool_from_database(conn)
    missing = [n for n in selected if n not in pool]
    if missing:
        raise ValueError(f"Selected trials missing from the pool: {missing}")
    all_cases = []
    for n in selected:
        all_cases.extend(write_trial(FOLDER / "naturalistic" / n, n, pool[n]))
    write_json(FOLDER / "reserve_inputs.json", {n: {"snapshot": pool[n]["snapshot"].model_dump(mode="json"),
        "safety": pool[n]["safety"].model_dump(mode="json"), "selected": pool[n]["selected"],
        "skipped": pool[n]["skipped"], "strict_holdout_pmids": pool[n]["strict_holdout_pmids"]} for n in reserve if n in pool})
    write_json(FOLDER / "draw.json", {"seed": SEED, "candidate_trials": candidates, "other_trials": others[:OTHER_TRIALS],
                                      "selected": selected, "reserve": reserve})
    strict = [c for c in all_cases if c["strict_holdout"]]
    first_per_trial = [next(c for c in strict if c["nct_id"] == n) for n in selected if any(c["nct_id"] == n for c in strict)]
    random.Random(SEED).shuffle(first_per_trial)
    blind = first_per_trial[:20]
    write_json(FOLDER / "blind_subset.json", {"case_ids": [c["case_id"] for c in blind],
        "instruction": "Independently label these cases before viewing any AI draft references. None have been drafted."})
    text = "# Independently label these abstracts first\n\nNo AI labels or model answers are included. Read protocol.md.\n\n"
    for c in blind:
        n = c["nct_id"]
        text += f"## {c['case_id']}\n\n[Registry](naturalistic/{n}/registry.md) · [Fill references](naturalistic/{n}/references.jsonl)\n\n"
        text += blind_sheet(n, [c]) + "\n"
    (FOLDER / "BLIND_SUBSET.md").write_text(text, encoding="utf-8")
    write_json(FOLDER / "reference_review.json", {"reviewer": None, "completed": False, "reviewed_at": None,
        "blind_subset_completed": False, "blind_subset_labelled_before_ai_drafts": False,
        "blind_subset_reviewer": None, "blind_subset_reviewed_at": None, "notes": ""})
    index = "# Safety v6 reference review\n\nStart with [the blind subset](BLIND_SUBSET.md) and [protocol.md](protocol.md). All references are blank.\n\n| Trial | Abstracts | Sources | Registry | Reference file |\n|---|---:|---|---|---|\n"
    for n in selected:
        count = sum(c["nct_id"] == n for c in all_cases)
        index += f"| {n} | {count} | [read](naturalistic/{n}/sheet.md) | [read](naturalistic/{n}/registry.md) | [fill](naturalistic/{n}/references.jsonl) |\n"
    (FOLDER / "REVIEW_INDEX.md").write_text(index, encoding="utf-8")
    names = ("cases.json", "safety.json", "snapshot.json", "selection.json", "sheet.md", "registry.md", "system_candidate.json",
             "reserve_inputs.json", "draw.json", "blind_subset.json", "BLIND_SUBSET.md", "RULE.md", "protocol.md")
    write_json(FOLDER / "manifest.json", {"prepared_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
        "selected_trials": selected, "reserve_trials": reserve, "naturalistic_cases": len(all_cases),
        "strict_unseen_cases": len(strict), "targeted_cases": 0, "excluded_prior_pmids": excluded_pmids, "excluded_prior_texts": excluded_texts,
        "input_hashes": {str(p.relative_to(FOLDER)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in FOLDER.rglob("*") if p.is_file() and p.name in names},
        "status": "Blank references; independent review outstanding; no model called"})
    print(json.dumps({"trials": len(selected), "candidates": len(candidates), "abstracts": len(all_cases), "strict": len(strict), "blind": len(blind)}))


if __name__ == "__main__":
    main()
