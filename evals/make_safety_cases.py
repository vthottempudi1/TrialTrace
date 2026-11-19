##############################################################################
# evals/make_safety_cases.py
#
# PURPOSE: Safety evaluation (step S9): choose the trials by a written rule and write the files a
#          person labels from. No model is called and no system answer is shown.
#
# STEPS:
#   1. Pool: stored trials with registry adverse-event data, 2 to 5 listed papers with an abstract,
#      never read by read-safety (the prompt was tried on those).
#   2. Draw: shuffle the pool with a fixed seed and take the first TRIALS trials (evals/safety/RULE.md).
#   3. Per trial, write into evals/safety/trials/<NCT>/:
#        blind_sheet.md          each abstract with numbered sentences
#        registry.md             the registry's groups and every listed event with its numbers
#        cases.unlabelled.jsonl  one line per abstract (PMID, file hash, fingerprint of the text)
#        labels.txt              empty, with the format in its header
#   It refuses to overwrite: labels are never lost by running it twice.
#
#   Amendment 2026-11-11: --extend adds every remaining trial of the draw (evals/safety/RULE.md). The first 18
#   stay listed in trials.txt; the added ones are listed in trials_extension.txt.
#
# USE:     python -m evals.make_safety_cases            # the first draw (done once)
#          python -m evals.make_safety_cases --extend   # the rest of the pool (done once)
##############################################################################

import hashlib
import json
import random
import sys
from pathlib import Path

import psycopg

from trialtrace.safety.safety_baseline import safety_sentence_spans
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_papers_for_trial, get_safety

SEED = 20261111
TRIALS = 18
MIN_PAPERS, MAX_PAPERS = 2, 5
FOLDER = Path("evals/safety")

LABEL_HEADER = """# Safety labels for {nct_id}. One line per safety statement, fields separated by " | ":
# PMID | sentence | kind | term | group | count | denominator | percent | grade | related | category
#   kind: death, serious or event.  term: the event's name (only for event), else -
#   group: as written in the sentence, or - (all participants / not stated)
#   count, denominator, percent, grade: as written in the sentence, or -
#   related: y if the sentence says treatment-related, else n
#   category: what a careful person concludes against registry.md (see evals/safety/rubric.md)
# An abstract with no safety statement gets one line:  PMID | none
"""


def count_text(affected, at_risk) -> str:
    if affected is None:
        return "not given"
    if at_risk is None:
        return f"{affected} (at risk not given)"
    return f"{affected} of {at_risk}"


def listed_abstracts(conn, nct_id: str) -> list[dict]:
    """The newest download of each listed paper that has an abstract, lowest PMID first."""
    newest = {}
    for paper in get_papers_for_trial(conn, nct_id):
        if paper["status"] == "listed" and paper["abstract"] and paper["pmid"] not in newest:
            newest[paper["pmid"]] = paper
    return sorted(newest.values(), key=lambda paper: int(paper["pmid"]))


def find_pool(conn) -> list[str]:
    """Every trial that fits the rule, in NCT order."""
    already_read = set()
    for (nct_id,) in conn.execute("SELECT DISTINCT nct_id FROM safety_readings").fetchall():
        already_read.add(nct_id)
    pool = []
    rows = conn.execute("SELECT nct_id FROM latest_snapshots WHERE safety_status = 'ok' ORDER BY nct_id").fetchall()
    for (nct_id,) in rows:
        if nct_id in already_read:
            continue
        if MIN_PAPERS <= len(listed_abstracts(conn, nct_id)) <= MAX_PAPERS:
            pool.append(nct_id)
    return pool


def blind_sheet(nct_id: str, papers: list[dict]) -> str:
    lines = [f"# {nct_id}: abstracts to label (no system answers shown)", ""]
    for paper in papers:
        lines.append(f"## PMID {paper['pmid']}: {paper['title']}")
        lines.append("")
        abstract = paper["abstract"]
        for index, (start, end) in enumerate(safety_sentence_spans(abstract)):
            lines.append(f"{index + 1}. {abstract[start:end].strip()}")
        lines.append("")
    return "\n".join(lines)


def registry_sheet(nct_id: str, safety) -> str:
    threshold = "not given"
    if safety.frequency_threshold is not None:
        threshold = f"{safety.frequency_threshold:g} %"
    lines = [f"# {nct_id}: the registry's adverse-event table", ""]
    lines.append(f"Time frame: {safety.time_frame or 'not given'}")
    lines.append(f"Listing threshold for non-serious events: {threshold}")
    lines.append("")
    lines.append("| Group | Deaths | Serious | Other |")
    lines.append("|---|---|---|---|")
    titles = {}
    for arm in safety.arms:
        titles[arm.group_id] = arm.title
        lines.append(f"| {arm.title} | {count_text(arm.deaths_affected, arm.deaths_at_risk)} | "
                     f"{count_text(arm.serious_affected, arm.serious_at_risk)} | {count_text(arm.other_affected, arm.other_at_risk)} |")
    lines.append("")
    lines.append("## Listed events (kind, term: numbers per group)")
    for event in safety.events:
        numbers = []
        for count in event.counts:
            numbers.append(f"{titles.get(count.group_id, count.group_id)}: {count_text(count.affected, count.at_risk)}")
        lines.append(f"- {event.kind}, {event.term or '(no term)'}: {'; '.join(numbers)}")
    return "\n".join(lines) + "\n"


def write_trial(conn, nct_id: str) -> int:
    """Write one trial's four files. Returns how many abstracts it has."""
    folder = FOLDER / "trials" / nct_id
    folder.mkdir(parents=True, exist_ok=False)          # refuses to overwrite
    snapshot = get_latest_snapshot(conn, nct_id)
    safety = get_safety(conn, nct_id, snapshot.content_hash)
    papers = listed_abstracts(conn, nct_id)

    cases = []
    for paper in papers:
        cases.append(json.dumps({
            "nct_id": nct_id, "pmid": paper["pmid"], "paper_raw_hash": paper["raw_hash"],
            "registry_hash": snapshot.content_hash,
            "abstract_sha256": hashlib.sha256(paper["abstract"].encode("utf-8")).hexdigest(),
        }))
    (folder / "blind_sheet.md").write_text(blind_sheet(nct_id, papers), encoding="utf-8")
    (folder / "registry.md").write_text(registry_sheet(nct_id, safety), encoding="utf-8")
    (folder / "cases.unlabelled.jsonl").write_text("\n".join(cases) + "\n", encoding="utf-8")
    (folder / "labels.txt").write_text(LABEL_HEADER.format(nct_id=nct_id), encoding="utf-8")
    return len(papers)


def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    if (FOLDER / "draw.txt").exists():
        raise SystemExit("evals/safety/draw.txt already exists: the draw is made once")
    with psycopg.connect(settings.database_url) as conn:
        pool = find_pool(conn)
        order = list(pool)
        random.Random(SEED).shuffle(order)
        chosen = order[:TRIALS]
        FOLDER.mkdir(parents=True, exist_ok=True)
        (FOLDER / "draw.txt").write_text("\n".join(order) + "\n", encoding="utf-8")       # the whole order, for the record
        (FOLDER / "trials.txt").write_text("\n".join(chosen) + "\n", encoding="utf-8")
        abstracts = 0
        for nct_id in chosen:
            abstracts += write_trial(conn, nct_id)
    print(f"Pool: {len(pool)} trials. Chosen: {len(chosen)} trials, {abstracts} abstracts. Files in {FOLDER / 'trials'}.")


def extend() -> None:
    """Add every trial of the draw that is not in trials.txt, in draw order. The first trials are not touched."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    if (FOLDER / "trials_extension.txt").exists():
        raise SystemExit("evals/safety/trials_extension.txt already exists: the extension is made once")
    order = (FOLDER / "draw.txt").read_text(encoding="utf-8").split()
    first = (FOLDER / "trials.txt").read_text(encoding="utf-8").split()
    added = []
    for nct_id in order:
        if nct_id not in first:
            added.append(nct_id)
    with psycopg.connect(settings.database_url) as conn:
        abstracts = 0
        for nct_id in added:
            abstracts += write_trial(conn, nct_id)
    (FOLDER / "trials_extension.txt").write_text("\n".join(added) + "\n", encoding="utf-8")
    print(f"Added: {len(added)} trials, {abstracts} abstracts. Listed in {FOLDER / 'trials_extension.txt'}.")


if __name__ == "__main__":
    if "--extend" in sys.argv:
        extend()
    else:
        main()
