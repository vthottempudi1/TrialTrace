##############################################################################
# evals/apply_labels.py
#
# PURPOSE: Write labels from a text file into a cases file. One line per case:
#            v01: y, primary, 11      (yes, the role the abstract gives, the sentence number)
#            v02: n                   (no)
#            v03: u                   (unclear)
#          An optional note can follow a "|". Every line is checked before anything is saved,
#          and a case that already has a label is never overwritten (use evals.label --redo).
#
# USE:     python -m evals.apply_labels evals/v2/labels_suggested_review.txt --cases evals/v2/cases.jsonl
##############################################################################

import argparse
from pathlib import Path

from evals.label import CASES_FILE, ROLES, load_cases, make_label, save_cases

ANSWERS = {"y": "yes", "n": "no", "u": "unclear"}


def parse_line(line: str) -> tuple[str, str, str | None, int | None, str]:
    """One line -> (case id, answer, role, sentence number, note). Raises ValueError if it is wrong."""
    text, _, note = line.partition("|")
    case_id, separator, answer_text = text.partition(":")
    if not separator:
        raise ValueError(f"no ':' in line: {line!r}")
    case_id = case_id.strip()
    parts = [part.strip() for part in answer_text.split(",")]
    letter = parts[0].lower()
    if letter not in ANSWERS:
        raise ValueError(f"{case_id}: the answer must be y, n or u, not {parts[0]!r}")
    answer = ANSWERS[letter]
    if answer != "yes":
        if len(parts) != 1:
            raise ValueError(f"{case_id}: only a yes has a role and a sentence number")
        return case_id, answer, None, None, note.strip()
    if len(parts) != 3:
        raise ValueError(f"{case_id}: a yes needs 'y, role, sentence number'")
    role = parts[1].lower()
    if role not in ROLES.values():
        raise ValueError(f"{case_id}: the role must be one of {list(ROLES.values())}, not {parts[1]!r}")
    if not parts[2].isdigit():
        raise ValueError(f"{case_id}: the sentence number must be a number, not {parts[2]!r}")
    return case_id, answer, role, int(parts[2]), note.strip()


def apply_labels(label_lines: list[str], cases: list[dict], source_name: str) -> int:
    """Put the labels on the cases (in memory). Returns how many were labelled. Raises ValueError on any problem."""
    by_id = {}
    for case in cases:
        by_id[case["case_id"]] = case

    seen = set()
    count = 0
    for line in label_lines:
        if not line.strip():
            continue
        case_id, answer, role, sentence, note = parse_line(line)
        if case_id not in by_id:
            raise ValueError(f"{case_id}: no such case")
        if case_id in seen:
            raise ValueError(f"{case_id}: given twice")
        seen.add(case_id)
        case = by_id[case_id]
        if case["label"] is not None:
            raise ValueError(f"{case_id}: already has a label (use evals.label --redo)")
        label = make_label(answer, role, sentence, case["abstract"], note)
        label["annotator"] = f"single annotator (project owner); labels read from {source_name}"
        case["label"] = label
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.apply_labels")
    parser.add_argument("labels_file", help="text file with one label line per case")
    parser.add_argument("--cases", default=str(CASES_FILE), help="the cases file to write the labels into")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    cases = load_cases(cases_path)
    label_lines = Path(args.labels_file).read_text(encoding="utf-8").splitlines()
    try:
        count = apply_labels(label_lines, cases, Path(args.labels_file).name)
    except ValueError as error:
        raise SystemExit(f"Nothing saved. {error}")
    save_cases(cases, cases_path)
    unlabelled = [case["case_id"] for case in cases if case["label"] is None]
    print(f"Labelled {count} cases in {cases_path}. Still unlabelled: {len(unlabelled)} {unlabelled}")


if __name__ == "__main__":
    main()
