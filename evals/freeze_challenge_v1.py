##############################################################################
# evals/freeze_challenge_v1.py
#
# PURPOSE: Step 4 of challenge set v1: build the FINAL labels (the draft labels with the reasoned reconciliation
#          decisions applied), check that the locked system is unchanged and that no model has read these trials,
#          and freeze everything by hash in freeze.md. The original agreement (before reconciliation) is kept apart
#          from the agreement after reconciliation. Refuses to run until Venu's labels, PROVENANCE.md and a decision
#          for every disagreement exist. Makes no model call.
#
# USE:     python -m evals.freeze_challenge_v1
##############################################################################

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals.challenge_tools import MODES, apply_reconciliation, compare_labels, read_tags
from evals.compare_challenge_v1 import FOLDER, SUBSET, check_provenance, read_subset_labels
from evals.run_multipaper import read_cases, read_labels
from trialtrace.config import Settings
from trialtrace.paper_reader import SCHEMA_VERSION, prompt_hash

LOCK = Path(__file__).parent / "validation_v1" / "system_lock.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if (FOLDER / "freeze.md").exists():
        raise SystemExit("freeze.md already exists; the labels are frozen.")
    check_provenance()
    venu = read_subset_labels()
    original = json.loads((FOLDER / "original_agreement.json").read_text(encoding="utf-8"))
    decisions_file = FOLDER / "reconciliation.json"
    if not decisions_file.exists():
        raise SystemExit("reconciliation.json is missing: decide every disagreement from the source evidence first (RECONCILIATION.md).")
    decisions = json.loads(decisions_file.read_text(encoding="utf-8"))

    drafts, tags, trials = {}, {}, []
    for trial in sorted((FOLDER / "cases").iterdir()):
        trials.append(trial.name)
        for case_id, label in read_labels(trial / "labels.txt").items():
            drafts[f"{trial.name}/{case_id}"] = label["answer"]
            tags[f"{trial.name}/{case_id}"] = read_tags(label["note"])
    disagreements = compare_labels(drafts, venu)["disagreements"]
    final = apply_reconciliation(drafts, decisions, disagreements)
    after = compare_labels(final, venu)

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    changed = [name for name, digest in lock["files_sha256"].items() if sha(Path(name)) != digest]
    if changed or prompt_hash() != lock["prompt_hash"] or SCHEMA_VERSION != lock["schema_version"]:
        raise SystemExit(f"The locked system changed since the lock: {changed or 'prompt or schema'}. Freeze refused.")
    with psycopg.connect(Settings().database_url) as conn:
        already = conn.execute("SELECT count(*) FROM paper_matches WHERE nct_id = ANY(%s) AND system = 'llm'", (trials,)).fetchone()[0]
    if already:
        raise SystemExit(f"{already} model answers already exist for these trials: freezing after a run is not allowed.")

    for key, decision in decisions.items():                      # a decision may also correct the failure-mode tags found on re-reading the source
        if "tags" in decision:
            unknown = [tag for tag in decision["tags"] if tag not in MODES]
            if unknown:
                raise SystemExit(f"{key}: unknown failure-mode tag {unknown}")
            tags[key] = decision["tags"]
    final_labels = {key: {"final": final[key], "draft": drafts[key], "venu": venu.get(key), "tags": tags[key],
                          "source": "reconciled" if key in decisions else "draft"} for key in sorted(final)}
    (FOLDER / "final_labels.json").write_text(json.dumps(final_labels, indent=2), encoding="utf-8")

    lines = ["# Challenge set v1: freeze record", "",
             f"Written {datetime.now(timezone.utc).isoformat(timespec='seconds')}, after reconciliation and BEFORE `match-papers` ran on these trials (no model answer exists for them).", "",
             f"- Pairs: {len(final)} in {len(trials)} trials. Final labels: yes {sum(v == 'yes' for v in final.values())}, unclear {sum(v == 'unclear' for v in final.values())}, no {sum(v == 'no' for v in final.values())}.",
             f"- Label authorship: draft labels by an AI model from the abstracts alone; Venu's blind subset ({len(venu)} pairs) as recorded in `venu_subset/PROVENANCE.md`; "
             "disagreements settled from the source evidence in `reconciliation.json`.",
             f"- ORIGINAL agreement (draft vs Venu, before reconciliation): {original['agree']}/{original['pairs']}"
             + (f", kappa {original['kappa']:.2f}" if original.get("kappa") is not None else "") + ".",
             f"- Agreement AFTER reconciliation (final vs Venu): {after['agree']}/{after['pairs']}. This is not independent agreement: it follows from the decisions.",
             f"- Disagreements reconciled: {len(decisions)}; labels changed from the draft: {sum(1 for k in decisions if final[k] != drafts[k])}. Decided by AI reviewer (AI) alone from the source text; Venu did not take part (`reconciliation_notes.md`).",
             f"- Locked system verified unchanged (prompt hash {prompt_hash()}, schema {SCHEMA_VERSION}); 0 model answers existed for these trials.", "",
             "| File | SHA-256 |", "|---|---|"]
    files = [FOLDER / "RULE.md", LOCK, FOLDER / "draw.txt", FOLDER / "selection.json", Path("evals/rubric.md"), FOLDER / "final_labels.json",
             FOLDER / "reconciliation.json", FOLDER / "reconciliation_notes.md", FOLDER / "original_agreement.json", FOLDER / "RECONCILIATION.md",
             SUBSET / "PROVENANCE.md", SUBSET / "pairs_1.json", SUBSET / "pairs_2.json", SUBSET / "venu_labels_1.txt", SUBSET / "venu_labels_2.txt"]
    for trial in trials:
        files += [FOLDER / "cases" / trial / "labels.txt", FOLDER / "cases" / trial / "cases.unlabelled.jsonl"]
    for path in files:
        lines.append(f"| `{path.as_posix()}` | {sha(path)} |")
    (FOLDER / "freeze.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
