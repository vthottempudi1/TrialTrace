"""One-time larger extension of v6 (RULE.md Amendment 1): all current reserve plus the first 12 eligible new trials."""
import json
from pathlib import Path

import psycopg

from evals import prepare_safety_v2 as legacy
from evals.prepare_safety_v4 import write_trial
from evals.run_safety_v4 import digest, items, now, read_json, write_json
from trialtrace.config import Settings
from trialtrace.contracts import SafetyExtraction, Snapshot

FOLDER = Path("evals/safety_v6")
ACQUISITION = Path("evals/safety_v6_acquisition")


def main():
    items(FOLDER, require_labels=False)
    manifest = read_json(FOLDER / "manifest.json")
    draw = read_json(ACQUISITION / "draw.json")
    screened = {t["nct_id"]: t for t in read_json(ACQUISITION / "screen.json")["trials"]}
    eligible_new = [n for n in draw["more"] if n in screened]
    flagged = [n for n in eligible_new if screened[n]["numeric_candidate"] or screened[n]["window_candidate"]]
    plain = [n for n in eligible_new if n not in flagged]
    new_trials = (flagged + plain)[:12]
    legacy.FOLDER = FOLDER
    with psycopg.connect(Settings().database_url) as conn:
        pool = legacy.pool_from_database(conn)[0]
    reserve = read_json(FOLDER / "reserve_inputs.json")
    for n in new_trials:
        reserve[n] = {"snapshot": pool[n]["snapshot"].model_dump(mode="json"), "safety": pool[n]["safety"].model_dump(mode="json"),
                      "selected": json.loads(json.dumps(pool[n]["selected"], default=str)),
                      "skipped": json.loads(json.dumps(pool[n]["skipped"], default=str)),
                      "strict_holdout_pmids": pool[n]["strict_holdout_pmids"]}
    write_json(FOLDER / "reserve_inputs.json", reserve)
    added = list(manifest["reserve_trials"]) + new_trials
    added_cases = []
    for nct in added:
        record = dict(reserve[nct])
        record["snapshot"] = Snapshot(**record["snapshot"])
        record["safety"] = SafetyExtraction(**record["safety"])
        added_cases.extend(write_trial(FOLDER / "naturalistic" / nct, nct, record))
    manifest["selected_trials"] += added
    manifest["reserve_trials"] = []
    manifest["naturalistic_cases"] += len(added_cases)
    manifest["strict_unseen_cases"] += sum(c["strict_holdout"] for c in added_cases)
    for path in (FOLDER / "naturalistic").rglob("*"):
        if path.is_file() and path.name in ("cases.json", "safety.json", "snapshot.json", "selection.json", "sheet.md", "registry.md"):
            manifest["input_hashes"][str(path.relative_to(FOLDER))] = digest(path)
    for name in ("reserve_inputs.json",):
        manifest["input_hashes"][name] = digest(FOLDER / name)
    manifest.setdefault("extensions", []).append({"at": now(), "trials": added, "one_time_larger_extension": True,
                                                  "strict_target_count_before": 39})
    write_json(FOLDER / "manifest.json", manifest)
    with (FOLDER / "REVIEW_INDEX.md").open("a", encoding="utf-8") as out:
        for nct in added:
            out.write(f"| Extension 2 {nct} | {sum(c['nct_id'] == nct for c in added_cases)} | [read](naturalistic/{nct}/sheet.md) | [read](naturalistic/{nct}/registry.md) | [fill](naturalistic/{nct}/references.jsonl) |\n")
    print("added trials", len(added), "abstracts", len(added_cases), "strict", sum(c["strict_holdout"] for c in added_cases))


if __name__ == "__main__":
    main()
