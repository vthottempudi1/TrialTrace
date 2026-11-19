##############################################################################
# evals/compare_challenge_v1.py
#
# PURPOSE: Step 3 of challenge set v1 (order in the rule): compare the draft labels with Venu's blind labels,
#          report the ORIGINAL agreement (before any reconciliation), and write RECONCILIATION.md: every disagreement
#          with both labels and notes, the registered outcome and the abstract sentences, and an empty line for the
#          decision. Decisions are then made from the source evidence and saved in reconciliation.json by hand;
#          neither original label file is edited. Refuses to run while any Venu line is unfilled or PROVENANCE.md is blank.
#
# USE:     python -m evals.compare_challenge_v1
##############################################################################

import hashlib
import json
import re
from pathlib import Path

from evals.challenge_tools import cohens_kappa, compare_labels
from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases, read_labels

FOLDER = Path(__file__).parent / "challenge_v1"
SUBSET = FOLDER / "venu_subset"
ANSWERS = ("yes", "no", "unclear")


LABEL_LINE = re.compile(r"^(NCT\d{8}/m\d+-\d+) \|")


def read_subset_labels() -> dict[str, str]:
    """Venu's answers as {'NCT/case': answer}. Only lines that start with a pair id count; any other text in the file
    (comments, stray pasted lines) is ignored and reported. Stops if a pair line has no valid answer, or if the pairs
    found are not exactly the 50 pairs of the blind subset."""
    answers, ignored = {}, []
    for name in ("venu_labels_1.txt", "venu_labels_2.txt"):
        for number, line in enumerate((SUBSET / name).read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip() or line.startswith("#"):
                continue
            if not LABEL_LINE.match(line):
                ignored.append(f"{name} line {number}: {line.strip()[:60]}")
                continue
            parts = [part.strip() for part in line.split("|", 4)]
            if len(parts) != 5 or parts[1] not in ANSWERS:
                raise SystemExit(f"{name} line {number}: needs an answer of yes, no or unclear (found {parts[1] if len(parts) > 1 else line!r})")
            if parts[0] in answers:
                raise SystemExit(f"{name} line {number}: {parts[0]} is labelled twice")
            answers[parts[0]] = parts[1]
    expected = set()
    for name in ("pairs_1.json", "pairs_2.json"):
        expected |= {f"{item['nct_id']}/{item['case_id']}" for item in json.loads((SUBSET / name).read_text(encoding="utf-8"))}
    if set(answers) != expected:
        raise SystemExit(f"The labelled pairs are not the blind subset: missing {sorted(expected - set(answers))[:3]}, extra {sorted(set(answers) - expected)[:3]}")
    if ignored:
        print(f"Ignored {len(ignored)} line(s) that are not labels (the files are not edited):")
        for item in ignored:
            print("  ", item)
    return answers


def check_provenance() -> None:
    """The provenance note must name who filled in the lines, who reviewed them, and the date, and its file hashes must match
    the label files as they are now. A sentence that calls AI-filled lines independent human labelling is reported, never used."""
    text = (SUBSET / "PROVENANCE.md").read_text(encoding="utf-8")
    for needed in ("Designated reviewer", "Filled by", "Date finished"):
        if needed not in text:
            raise SystemExit(f"PROVENANCE.md does not say '{needed}'. Label authorship has to be recorded as it happened.")
    for name in ("venu_labels_1.txt", "venu_labels_2.txt"):
        digest = hashlib.sha256((SUBSET / name).read_bytes()).hexdigest()
        if digest not in text:
            raise SystemExit(f"PROVENANCE.md records a different hash for {name}: the file changed after the note was written.")
    if re.search(r"language-model|ai assistant|language model", text, re.IGNORECASE) and re.search(r"independent human label", text, re.IGNORECASE):
        print("WARNING: PROVENANCE.md says the lines were filled by a language model AND calls them independent human labelling. "
              "They are reported here as language-model-filled and reviewed by Venu, with limited independence.")


def main() -> None:
    check_provenance()
    venu = read_subset_labels()
    mine, notes, cases = {}, {}, {}
    for trial in sorted((FOLDER / "cases").iterdir()):
        labels = read_labels(trial / "labels.txt")
        for case_id, case in read_cases(trial / "cases.unlabelled.jsonl").items():
            key = f"{trial.name}/{case_id}"
            mine[key], notes[key], cases[key] = labels[case_id]["answer"], labels[case_id]["note"], case
    unknown = sorted(set(venu) - set(mine))
    if unknown:
        raise SystemExit(f"Venu labelled pairs that are not in the set: {unknown[:5]}")

    result = compare_labels(mine, venu)
    kappa = cohens_kappa(mine, venu)
    lines = ["# Challenge set v1: reconciliation of draft labels and Venu's blind labels", "",
             f"ORIGINAL agreement (before any reconciliation): {result['agree']}/{result['pairs']} ({100 * result['share']:.0f}%), kappa {kappa:.2f}." if kappa is not None
             else f"ORIGINAL agreement: {result['agree']}/{result['pairs']}.",
             f"Confusion (draft -> Venu): {result['confusion']}", "",
             "Resolve each disagreement from the SOURCE evidence below (the abstract and the registered outcome), not by preference. "
             "Record each decision in `reconciliation.json` as {\"NCT/case\": {\"final\": yes|no|unclear, \"reason\": \"...\", \"sentences\": [n]}}. "
             "The draft file and Venu's files stay as they are. After reconciliation, report BOTH the original agreement above and the agreement "
             "after reconciliation, and keep the two apart.", ""]
    for number, (key, draft, other) in enumerate(result["disagreements"], start=1):
        case = cases[key]
        registered = case["registered"]
        lines += [f"## {number}. {key}", f"Registered outcome ({registered['role']}): {registered['measure_text']}", f"Time frame: {registered['time_frame'] or 'not given'}",
                  f"Draft label: **{draft}**. Note: {notes[key]}", f"Venu: **{other}**", "", "Abstract:"]
        for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
        lines += ["", "Decision: ______   Reason from the source: ______", ""]
    (FOLDER / "RECONCILIATION.md").write_text("\n".join(lines), encoding="utf-8")
    (FOLDER / "original_agreement.json").write_text(json.dumps({"pairs": result["pairs"], "agree": result["agree"], "share": result["share"], "kappa": kappa,
                                                                 "confusion": result["confusion"]}, indent=2), encoding="utf-8")
    print(lines[2])
    print(f"{len(result['disagreements'])} disagreements written to RECONCILIATION.md")


if __name__ == "__main__":
    main()
