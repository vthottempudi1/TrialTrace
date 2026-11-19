##############################################################################
# evals/label.py
#
# PURPOSE: Label the evaluation cases by reading, one at a time. You see the registered
#          outcome and the paper's abstract (sentences numbered). You never see what the
#          baseline or an LLM says, so your labels cannot be influenced by them.
#          You are the ONLY labeller: this is declared in every label and in the results.
#          Each answer is saved at once; you can quit and continue later.
#
# QUESTIONS PER CASE:
#   1. Does the abstract report THIS registered outcome (the same thing measured)?
#        y = yes    n = no    u = unclear (cannot tell from the abstract)
#   2. If yes: what role does the abstract give it?  p = primary  s = secondary  n = not stated
#   3. If yes: the number of the sentence that reports it (so the quote is exact).
#
# RULES:   Read only the abstract. Do not look anything up. "No" means "not in the abstract",
#          not "not in the paper". When unsure, answer u; that is a real answer, not a failure.
#
# USE:     python -m evals.label            (continue with the unlabelled cases)
#          python -m evals.label --redo c07 (label one case again; the old label is kept in its history)
##############################################################################

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from trialtrace.compare.abstract_baseline import sentence_spans

CASES_FILE = Path(__file__).parent / "cases.jsonl"
ANNOTATOR = "single annotator (project owner)"
REPORTED = {"y": "yes", "n": "no", "u": "unclear"}
ROLES = {"p": "primary", "s": "secondary", "n": "not_stated"}


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    """Read one JSON object per line, skipping empty lines."""
    cases = []
    file_text = path.read_text(encoding="utf-8")
    for line in file_text.splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def save_cases(cases: list[dict], path: Path = CASES_FILE) -> None:
    """Write the whole file, then swap it in, so a crash never leaves half a file."""
    temporary = path.with_suffix(".tmp")
    lines = []
    for case in cases:
        lines.append(json.dumps(case, ensure_ascii=False))
    file_text = "\n".join(lines) + "\n"
    temporary.write_text(file_text, encoding="utf-8")
    temporary.replace(path)


def make_label(reported: str, role: str | None, sentence: int | None, abstract: str, notes: str = "") -> dict:
    """The label for one case. A quote is stored with its exact position in the abstract."""
    if reported not in REPORTED.values():
        raise ValueError(f"reported must be one of {list(REPORTED.values())}")
    label = {
        "reported": reported,
        "reported_role": None,
        "quote": None,
        "notes": notes.strip(),
        "annotator": ANNOTATOR,
        "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Only a yes answer needs a role and a supporting sentence.
    if reported == "yes":
        if role not in ROLES.values():
            raise ValueError(f"a 'yes' needs a role: {list(ROLES.values())}")
        spans = sentence_spans(abstract)
        if sentence is None or not 1 <= sentence <= len(spans):
            raise ValueError(f"a 'yes' needs a sentence number from 1 to {len(spans)}")
        # The person counts from 1; Python list positions start at 0.
        start, end = spans[sentence - 1]
        label["reported_role"] = role
        label["quote"] = {"text": abstract[start:end], "start": start, "end": end}
    return label


def show_case(case: dict, position: int, total: int, show: Callable[[str], None]) -> None:
    registered = case["registered"]
    show("")
    show("=" * 78)
    show(f"Case {case['case_id']}  ({position}/{total})  trial {case['nct_id']}  paper {case['paper']['pmid']}")
    show("-" * 78)
    show(f"REGISTERED OUTCOME ({registered['role']}):")
    show(f"  {registered['measure_text']}")
    show(f"  time frame: {registered['time_frame'] or 'not given'}")
    show("")
    show("ABSTRACT (sentences numbered):")
    for number, (start, end) in enumerate(sentence_spans(case["abstract"]), start=1):
        show(f"  [{number}] {case['abstract'][start:end]}")


def ask_choice(question: str, choices: dict[str, str], ask: Callable[[str], str]) -> str:
    """Keep asking until the answer is one of the allowed letters."""
    while True:
        answer = ask(f"{question} ({'/'.join(choices)}): ").strip().lower()
        if answer in choices:
            return choices[answer]


def ask_sentence(count: int, ask: Callable[[str], str]) -> int:
    while True:
        answer = ask(f"Sentence number that reports it (1-{count}): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer)


def label_case(case: dict, ask: Callable[[str], str]) -> dict:
    """Ask the person for an answer; this function does not predict a label."""
    reported = ask_choice("Does the abstract report this outcome? y=yes n=no u=unclear", REPORTED, ask)
    role, sentence = None, None
    if reported == "yes":
        role = ask_choice("Role the abstract gives it? p=primary s=secondary n=not stated", ROLES, ask)
        sentence_count = len(sentence_spans(case["abstract"]))
        sentence = ask_sentence(sentence_count, ask)
    notes = ask("Note (optional, Enter to skip): ")
    return make_label(reported, role, sentence, case["abstract"], notes)


def run(redo: str | None = None, path: Path = CASES_FILE, ask: Callable[[str], str] = input,
        show: Callable[[str], None] = print) -> int:
    """Label the cases that have no label (or one case again). Returns how many were labelled now."""
    cases = load_cases(path)
    # A redo selects one specific case. Otherwise, select all unfinished cases.
    queue = []
    for case in cases:
        if redo:
            if case["case_id"] == redo:
                queue.append(case)
        elif case["label"] is None:
            queue.append(case)
    if redo and not queue:
        raise SystemExit(f"no case {redo}")
    done = 0
    for position, case in enumerate(queue, start=1):
        show_case(case, position, len(queue), show)
        new_label = label_case(case, ask)
        if case["label"] is not None:                       # a redo keeps the old label as history
            new_label["previous"] = case["label"]
        case["label"] = new_label
        # Save after every answer, so the person can stop and resume later.
        save_cases(cases, path)
        done += 1
        labelled_count = 0
        for saved_case in cases:
            if saved_case["label"] is not None:
                labelled_count += 1
        show(f"Saved. {labelled_count}/{len(cases)} cases labelled.")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.label")
    parser.add_argument("--redo", help="label one case again, e.g. c07")
    parser.add_argument("--cases", default=str(CASES_FILE), help="the cases file (default: the v1 cases)")
    args = parser.parse_args()
    try:
        run(args.redo, Path(args.cases))
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. Everything you answered is saved.", file=sys.stderr)


if __name__ == "__main__":
    main()
