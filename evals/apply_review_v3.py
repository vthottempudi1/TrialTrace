##############################################################################
# evals/apply_review_v3.py
#
# PURPOSE: Turn the reviewer's decisions (evals/v3/review_decisions.jsonl) into a REVIEWED
#          cases file that the scorer can read. Uses the v3 sentence numbering (not the older
#          splitter). Every decision is checked first; nothing is written if one is wrong.
#          A split is only written when EVERY case in it has a decision, so reviewed and
#          provisional labels are never mixed. The old suggestion is kept inside the new label.
#          Prints how many labels changed, by class.
#
# USE:     python -m evals.apply_review_v3 --split dev
#          python -m evals.run --system baseline --cases evals/v3/cases.reviewed.dev.jsonl
##############################################################################

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evals.prepare_v3 import read_jsonl, spans

V3_DIR = Path(__file__).parent / "v3"
ANSWERS = ("yes", "no", "unclear")
ROLES = ("primary", "secondary", "not_stated")


def check_decision(decision: dict, case: dict) -> None:
    """Raise ValueError if the decision is not complete and consistent."""
    case_id = decision["case_id"]
    reported = decision["reviewed_reported"]
    if reported not in ANSWERS:
        raise ValueError(f"{case_id}: reviewed_reported must be one of {ANSWERS}, not {reported!r}")
    if not decision.get("reviewer"):
        raise ValueError(f"{case_id}: reviewer is missing")
    if reported != "yes":
        if decision.get("reviewed_role") or decision.get("reviewed_sentence"):
            raise ValueError(f"{case_id}: only a yes has a role and a sentence number")
    else:
        if decision.get("reviewed_role") not in ROLES:
            raise ValueError(f"{case_id}: a yes needs reviewed_role, one of {ROLES}")
        sentence = decision.get("reviewed_sentence")
        count = len(spans(case["abstract"]))
        if not isinstance(sentence, int) or not 1 <= sentence <= count:
            raise ValueError(f"{case_id}: reviewed_sentence must be a number from 1 to {count}")
    # A change from the suggestion needs a reason, so the reasons for disagreement are on record.
    suggested = (case["label"] or {}).get("reported")        # None when the case has no earlier label
    if suggested is not None and reported != suggested and not (decision.get("reason") or "").strip():
        raise ValueError(f"{case_id}: you changed {suggested} to {reported}: please give a reason")


def annotator_text(decision: dict) -> str:
    """Who is on record: the reviewer, and who drafted the decision if it was not the reviewer."""
    reviewer = decision["reviewer"]
    drafted_by = decision.get("drafted_by")
    if drafted_by:
        return f"{reviewer} (reviewed and adopted decisions drafted by {drafted_by})"
    return f"{reviewer} (reviewer)"


def reviewed_label(decision: dict, case: dict) -> dict:
    """The new label. The old suggestion is kept inside it as `previous`."""
    reported = decision["reviewed_reported"]
    label = {
        "reported": reported,
        "reported_role": None,
        "quote": None,
        "notes": (decision.get("reason") or "").strip(),
        "annotator": annotator_text(decision),
        "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_status": "reviewed",
        "previous": case["label"],
    }
    if reported == "yes":
        start, end = spans(case["abstract"])[decision["reviewed_sentence"] - 1]
        label["reported_role"] = decision["reviewed_role"]
        label["quote"] = {"text": case["abstract"][start:end], "start": start, "end": end}
    return label


def build_reviewed(cases: list[dict], decisions: list[dict], split: str) -> tuple[list[dict], dict]:
    """The reviewed cases of one split (or all), and a summary of what changed."""
    by_id = {}
    for decision in decisions:
        if decision["reviewed_reported"] is not None:
            by_id[decision["case_id"]] = decision

    chosen = []
    for case in cases:
        if split == "all" or case["split"] == split:
            chosen.append(case)
    missing = []
    for case in chosen:
        if case["case_id"] not in by_id:
            missing.append(case["case_id"])
    if missing:
        raise ValueError(f"{len(missing)} {split} cases have no decision yet, for example {missing[:5]}")

    reviewed_cases = []
    changes = Counter()
    role_changes = 0
    sentence_changes = 0
    for case in chosen:
        decision = by_id[case["case_id"]]
        check_decision(decision, case)
        label = reviewed_label(decision, case)
        old = case["label"]
        if old is None:                                     # a fresh case: nothing to compare with
            changes[(None, label["reported"])] += 1
        else:
            changes[(old["reported"], label["reported"])] += 1
        if old is not None and old["reported"] == "yes" and label["reported"] == "yes":
            if old["reported_role"] != label["reported_role"]:
                role_changes += 1
            if old["quote"]["start"] != label["quote"]["start"]:
                sentence_changes += 1
        new_case = dict(case)
        new_case["label"] = label
        reviewed_cases.append(new_case)

    summary = {"cases": len(reviewed_cases), "changes": changes, "role_changes": role_changes, "sentence_changes": sentence_changes}
    return reviewed_cases, summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.apply_review_v3")
    parser.add_argument("--split", choices=["dev", "held_out", "all"], required=True)
    parser.add_argument("--decisions", default=str(V3_DIR / "review_decisions.jsonl"))
    parser.add_argument("--cases", default=str(V3_DIR / "cases.jsonl"))
    parser.add_argument("--out", help="the reviewed cases file to write (default: evals/v3/cases.reviewed.<split>.jsonl)")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else V3_DIR / f"cases.reviewed.{args.split}.jsonl"
    if out_path.exists():
        raise SystemExit(f"{out_path} already exists. Reviewed labels are not overwritten; use a new name to start over.")
    try:
        reviewed_cases, summary = build_reviewed(read_jsonl(Path(args.cases)), read_jsonl(Path(args.decisions)), args.split)
    except ValueError as error:
        raise SystemExit(f"Nothing written. {error}")

    lines = [json.dumps(case, ensure_ascii=False) for case in reviewed_cases]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {summary['cases']} reviewed cases to {out_path}")
    total_changed = 0
    for (old, new), count in sorted(summary["changes"].items(), key=str):
        if old is not None and old != new:
            total_changed += count
            print(f"  changed {old} -> {new}: {count}")
    print(f"Labels changed from the suggestions: {total_changed} of {summary['cases']}")
    print(f"Among kept yes labels: role changed {summary['role_changes']}, result sentence changed {summary['sentence_changes']}")


if __name__ == "__main__":
    main()
