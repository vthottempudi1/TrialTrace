##############################################################################
# evals/make_multipaper_cases.py
#
# PURPOSE: Build the cases for the trial-with-several-papers evaluation (Level A pairs, Level B roll-up).
#          One case = one registered outcome + one linked abstract. EVERY outcome is paired with EVERY paper
#          that the match run read, so the outcome-level truth can be worked out from your labels.
#          Writes, in evals/multipaper/<NCT_ID>/:
#            blind_sheet.md           what you read: each abstract once with numbered sentences, then each outcome
#            labels.txt               what you fill in: one line per pair
#            cases.unlabelled.jsonl   the same pairs for the scorer (label = null)
#          No system answer is shown anywhere (blind). Refuses to overwrite.
#
# USE:     python -m evals.make_multipaper_cases NCT04644237
##############################################################################

import argparse
import hashlib
import json
from pathlib import Path

import psycopg

from evals.prepare_v3 import spans
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_outcomes, get_paper_matches, get_papers_for_trial

OUT_DIR = Path(__file__).parent / "multipaper"


def papers_read_in_newest_run(conn, nct_id: str, before_run: bool = False) -> tuple[str, list[dict]]:
    """The newest match run, and the stored papers that run actually read (in PMID order).

    before_run=True (labels first, run later): no run exists yet, so use the papers a run WOULD read,
    the listed ones with an abstract. The scorer checks later that the run read exactly these.
    """
    matches = get_paper_matches(conn, nct_id)
    if not matches and before_run:
        papers = [p for p in get_papers_for_trial(conn, nct_id) if p["status"] == "listed" and p["abstract"]]
        papers.sort(key=lambda p: int(p["pmid"]))
        return "none yet", papers
    if not matches:
        raise SystemExit(f"{nct_id} has no match run; run match-papers first (or pass --before-run)")
    run_id = matches[0]["match_run_id"]
    read = {(row["pmid"], row["paper_raw_hash"]) for row in matches if row["match_run_id"] == run_id}
    papers = [p for p in get_papers_for_trial(conn, nct_id) if (p["pmid"], p["raw_hash"]) in read]
    papers.sort(key=lambda p: int(p["pmid"]))
    return run_id, papers


def build_cases(nct_id: str, content_hash: str, outcomes: list, papers: list[dict]) -> list[dict]:
    cases = []
    for number, outcome in enumerate(outcomes, start=1):
        for paper in papers:
            cases.append({
                "case_id": f"m{number:02d}-{paper['pmid']}",
                "nct_id": nct_id,
                "registry_content_hash": content_hash,
                "paper": {"pmid": paper["pmid"], "raw_hash": paper["raw_hash"],
                          "abstract_sha256": hashlib.sha256(paper["abstract"].encode("utf-8")).hexdigest(),
                          "link_status": paper["status"]},
                "registered": {"role": outcome.role, "position": outcome.position,
                               "measure_text": outcome.measure_text, "time_frame": outcome.time_frame},
                "abstract": paper["abstract"],
                "label": None,
            })
    return cases


def write_blind_sheet(path: Path, nct_id: str, outcomes: list, papers: list[dict]) -> None:
    lines = [f"# Blind sheet for {nct_id} (no system answers shown)", "",
             "Read the abstracts only. Do not look anything up. 'No' means 'not in the abstract', not 'not in the paper'.",
             "For every outcome and every paper decide: yes (this outcome's RESULT is reported) / no / unclear.",
             "For yes also give the role the abstract gives it and the number of the sentence that reports the result.",
             "Fill in labels.txt, not this file.", "", "## Abstracts", ""]
    for paper in papers:
        lines.append(f"### PMID {paper['pmid']}  (link check: {paper['status']})")
        lines.append(f"Title: {paper['title']}")
        for number, (start, end) in enumerate(spans(paper["abstract"]), start=1):
            lines.append(f"[{number}] {paper['abstract'][start:end]}")
        lines.append("")
    lines.append("## Registered outcomes")
    lines.append("")
    for number, outcome in enumerate(outcomes, start=1):
        lines.append(f"### Outcome {number:02d} ({outcome.role}): {outcome.measure_text}")
        lines.append(f"Time frame: {outcome.time_frame or 'not given'}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_labels_template(path: Path, cases: list[dict]) -> None:
    lines = ["# One line per pair: case_id | answer | role | sentence numbers | note",
             "#   answer: yes / no / unclear      role (only if yes): primary / secondary / not_stated",
             "#   sentences (only if yes): the RESULT sentence number(s) in that paper, e.g. 8 or 8,9",
             "#   Example:  m01-111 | yes | primary | 8 | ORR reported in sentence 8",
             "#   Example:  m02-111 | no | | | not mentioned",
             "# Replace each '?' ; keep the | separators."]
    for case in cases:
        registered = case["registered"]
        short = registered["measure_text"][:60]
        lines.append(f"{case['case_id']} | ? | | |      # {registered['role']}: {short}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nct_id")
    parser.add_argument("--before-run", action="store_true", help="labels first: pair every outcome with every listed paper that has an abstract")
    parser.add_argument("--out", default=str(OUT_DIR), help="folder that gets one sub-folder per trial")
    args = parser.parse_args()
    folder = Path(args.out) / args.nct_id
    if folder.exists():
        raise SystemExit(f"{folder} already exists; the cases are not overwritten.")

    with psycopg.connect(Settings().database_url) as conn:
        run_id, papers = papers_read_in_newest_run(conn, args.nct_id, args.before_run)
        snapshot = get_latest_snapshot(conn, args.nct_id)
        outcomes = get_outcomes(conn, args.nct_id, snapshot.content_hash).outcomes
    papers = [p for p in papers if p["abstract"]]          # a paper without an abstract has nothing to label

    cases = build_cases(args.nct_id, snapshot.content_hash, outcomes, papers)
    folder.mkdir(parents=True)
    (folder / "cases.unlabelled.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    write_blind_sheet(folder / "blind_sheet.md", args.nct_id, outcomes, papers)
    write_labels_template(folder / "labels.txt", cases)
    print(f"{args.nct_id}: match run {run_id}, {len(outcomes)} outcomes x {len(papers)} papers = {len(cases)} pairs -> {folder}")


if __name__ == "__main__":
    main()
