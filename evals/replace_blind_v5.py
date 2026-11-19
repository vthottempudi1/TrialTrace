"""Replace the v5 blind subset with 20 fresh abstracts chosen by code. Prints counts only, never abstract text."""
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals import prepare_safety_v2 as legacy
from evals.make_safety_cases import blind_sheet
from evals.prepare_safety_v4 import write_json, write_trial
from trialtrace.config import Settings

FOLDER = Path("evals/safety_v5")
ACQUISITION = Path("evals/safety_v5_acquisition")
SEED = 20261115
NAMES = ("cases.json", "safety.json", "snapshot.json", "selection.json", "sheet.md", "registry.md", "system_candidate.json",
         "reserve_inputs.json", "draw.json", "blind_subset.json", "BLIND_SUBSET.md", "RULE.md", "protocol.md")


def main():
    manifest = json.loads((FOLDER / "manifest.json").read_text(encoding="utf-8"))
    old_blind = json.loads((FOLDER / "blind_subset.json").read_text(encoding="utf-8"))
    draw = json.loads((ACQUISITION / "draw.json").read_text(encoding="utf-8"))
    new_ids = set(draw["selected"]) - set(draw["selected_before_extension_2"])
    legacy.FOLDER = FOLDER
    with psycopg.connect(Settings().database_url) as conn:
        pool = legacy.pool_from_database(conn)[0]
    eligible = sorted(n for n in pool if n in new_ids and n not in manifest["selected_trials"])
    if len(eligible) < 20:
        raise ValueError(f"Only {len(eligible)} eligible fresh trials; ingest more first")
    random.Random(SEED).shuffle(eligible)
    chosen = eligible[:20]
    cases, blind = [], []
    for n in chosen:
        written = write_trial(FOLDER / "naturalistic" / n, n, pool[n])
        cases.extend(written)
        blind.append(next(c for c in written if c["strict_holdout"]))
    history = FOLDER / "label_amendments"
    history.mkdir(exist_ok=True)
    write_json(history / "blind_subset_replaced.json", {"at": datetime.now(timezone.utc).isoformat(),
        "reason": "Original blind 20 had AI drafts before labelling; replaced by fresh abstracts per acquisition RULE Amendment 2",
        "replaced": old_blind, "fresh_trials": chosen, "fresh_unspent_eligible_trials": eligible[20:]})
    write_json(FOLDER / "blind_subset.json", {"case_ids": [c["case_id"] for c in blind],
        "instruction": "Label these cases yourself before anyone drafts or shows labels for them. Nobody has read or drafted them."})
    text = "# Independently label these 20 fresh abstracts first\n\nNobody has read or drafted these. Read protocol.md.\n\n"
    for c in blind:
        n = c["nct_id"]
        text += f"## {c['case_id']}\n\n[Registry](naturalistic/{n}/registry.md) · [Fill references](naturalistic/{n}/references.jsonl)\n\n"
        text += blind_sheet(n, [c]) + "\n"
    (FOLDER / "BLIND_SUBSET.md").write_text(text, encoding="utf-8")
    manifest["selected_trials"] += chosen
    manifest["naturalistic_cases"] += len(cases)
    manifest["strict_unseen_cases"] += sum(c["strict_holdout"] for c in cases)
    manifest.setdefault("amendments", []).append({"at": datetime.now(timezone.utc).isoformat(), "blind_subset_replaced": True, "added_trials": chosen})
    for path in FOLDER.rglob("*"):
        if path.is_file() and path.name in NAMES:
            manifest["input_hashes"][str(path.relative_to(FOLDER))] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(FOLDER / "manifest.json", manifest)
    review = json.loads((FOLDER / "reference_review.json").read_text(encoding="utf-8"))
    with (FOLDER / "review_history.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "prior_review": review, "added_trials": chosen}) + "\n")
    review.update(completed=False, reviewed_at=None, blind_subset_completed=False, blind_subset_labelled_before_ai_drafts=False,
                  blind_subset_reviewer=None, blind_subset_reviewed_at=None)
    review["notes"] = ("Blind subset replaced by 20 fresh unread abstracts (RULE Amendment 2). Earlier 107 cases remain reviewed by Venu. "
                       "Fresh cases need Venu's blind labels before any draft; then full review and freeze.")
    write_json(FOLDER / "reference_review.json", review)
    with (FOLDER / "REVIEW_INDEX.md").open("a", encoding="utf-8") as out:
        for n in chosen:
            out.write(f"| Fresh {n} | {sum(c['nct_id'] == n for c in cases)} | [read](naturalistic/{n}/sheet.md) | [read](naturalistic/{n}/registry.md) | [fill](naturalistic/{n}/references.jsonl) |\n")
    print({"fresh_trials": len(chosen), "fresh_abstracts": len(cases), "blind": len(blind), "unspent_eligible": len(eligible) - 20})


if __name__ == "__main__":
    main()
