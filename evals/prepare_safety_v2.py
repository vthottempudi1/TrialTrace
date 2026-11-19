"""Create an untouched safety holdout and blank human reference sheets. No model calls."""

import argparse
import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals.make_safety_cases import LABEL_HEADER, blind_sheet, registry_sheet
from trialtrace.match_papers import select_papers
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_papers_for_trial, get_safety, list_trial_ids

FOLDER = Path("evals/safety_v2")
SEED = 20261112
INITIAL_TRIALS = 30
MAX_ABSTRACTS = 10

RULE = """# Safety v2 holdout selection rule

Written before the draw, before reading holdout abstracts for labels, and before any evaluation call.

- Stored trials with registry safety data and at least one listed abstract.
- Exclude every trial with any previous safety reading. Exclude PMIDs and identical abstract text previously
  read by either model pipeline, previously included in project case/sheet files, or used by the safety helper.
  'Unseen' means unseen in the project's recorded evaluations/development, not absent from model pretraining.
- Sort trial IDs, shuffle once with seed 20261112, take the first 30 trials. The rest stay in draw order.
- For each eligible trial, apply the shared production selector: newest version per PMID, listed papers with
  abstracts only, numeric PMID ascending, first 10. Record every paper skipped beyond the cap.
- Keep previously exposed project cases if production selects them, but mark them coverage-only and exclude
  them from the strict unseen score. Production coverage and fully unseen trial counts are reported separately.
- No selection or exclusion by safety content, apparent results, label, or system answer.
- If the initial references contain fewer than 60 strict unseen safety statements, extend by the next 10 trials in the saved
  order before freezing, using the same preselected abstracts. Repeat until 60 statements or pool exhaustion.
  This stopping rule uses reference counts only, never system outputs. No performance-based redrawing.
- Human labels and matching/window references are written blind. No extraction/helper calls until completed
  reference review, full coverage validation and a hashed freeze. Old development sets are excluded.
- Empty/absent branches are not tested. Do not replace missing real branches with synthetic cases in the score.
- Full text, autonomous agents and a new registry wording helper are outside this iteration.
"""


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def previous_case_pmids():
    """Conservatively exclude prior case manifests and blind sheets, including unscored sets."""
    excluded = set()
    for path in Path("evals").rglob("cases*.jsonl"):
        if FOLDER in path.parents:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            data = json.loads(line)
            if data.get("pmid"):
                excluded.add(str(data["pmid"]))
    for path in Path("evals").rglob("*sheet.md"):
        if FOLDER not in path.parents:
            excluded.update(re.findall(r"PMID\s+(\d+)", path.read_text(encoding="utf-8")))
    for path in Path("outputs/safety-matches").glob("*/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        excluded.add(data["context"]["pmid"])
    return excluded


def choose_trials(pool, seed=SEED, count=INITIAL_TRIALS):
    order = sorted(pool)
    random.Random(seed).shuffle(order)
    return order[:count], order[count:]


def choose_abstracts(nct_id, papers):
    # Keep this compatibility wrapper; selection is identical to the product, not independently shuffled.
    return select_papers(papers, MAX_ABSTRACTS)[0]


def pool_from_database(conn):
    excluded = previous_case_pmids()
    safety_trials = {row[0] for row in conn.execute("SELECT DISTINCT nct_id FROM safety_readings")}
    seen_text = set()
    for table in ("paper_matches", "safety_readings"):
        rows = conn.execute(f"SELECT DISTINCT r.pmid, p.abstract FROM {table} r JOIN papers p "
                            "ON p.pmid=r.pmid AND p.raw_hash=r.paper_raw_hash WHERE r.system='llm'")
        for pmid, text in rows:
            excluded.add(pmid)
            if text:
                seen_text.add(sha(text))
    # Prior manifests can contain an abstract not yet read by a model.
    for pmid, text in conn.execute("SELECT pmid, abstract FROM papers WHERE abstract IS NOT NULL"):
        if pmid in excluded:
            seen_text.add(sha(text))
    pool = {}
    for nct_id in list_trial_ids(conn):
        if nct_id in safety_trials:
            continue
        snapshot = get_latest_snapshot(conn, nct_id)
        safety = get_safety(conn, nct_id, snapshot.content_hash) if snapshot else None
        if not safety or safety.status != "ok" or not safety.arms:
            continue
        newest = {}
        for paper in get_papers_for_trial(conn, nct_id):
            if paper["status"] == "listed" and paper["abstract"] and paper["pmid"] not in newest:
                newest[paper["pmid"]] = paper
        clean = [p for p in newest.values() if p["pmid"] not in excluded and sha(p["abstract"]) not in seen_text]
        if clean:
            pool[nct_id] = {"snapshot": snapshot, "safety": safety, "papers": clean,
                            "selected": choose_abstracts(nct_id, list(newest.values())),
                            "skipped": select_papers(list(newest.values()), MAX_ABSTRACTS)[1],
                            "strict_holdout_pmids": [p["pmid"] for p in clean]}
    return pool, len(excluded), len(seen_text)


def write_trial(folder, nct_id, data, replace_sources=False):
    folder.mkdir(parents=True, exist_ok=replace_sources)
    papers, safety = data["selected"], data["safety"]
    (folder / "sheet.md").write_text(blind_sheet(nct_id, papers), encoding="utf-8")
    table = registry_sheet(nct_id, safety)
    table += "\n## Group identity and scope\n\n"
    for arm in safety.arms:
        table += f"- arm:{arm.group_id}: {arm.title}\n  {arm.description or '(no description)'}\n"
    table += "\n## Event IDs for match references\n\n"
    for event in safety.events:
        table += f"- event:{event.kind}:{event.position}: {event.term}; notes: {event.notes or '(none)'}\n"
    (folder / "registry.md").write_text(table, encoding="utf-8")
    (folder / "safety.json").write_text(safety.model_dump_json(indent=2), encoding="utf-8")
    if not (folder / "labels.txt").exists():
        (folder / "labels.txt").write_text(LABEL_HEADER.format(nct_id=nct_id), encoding="utf-8")
    if not (folder / "match_labels.jsonl").exists():
        (folder / "match_labels.jsonl").write_text(
            '# One JSON object per labels.txt statement, in the same order. See ../../protocol.md.\n', encoding="utf-8")
    selection = {"paper_limit": MAX_ABSTRACTS, "method": "shared production selector; numeric PMID ascending",
                 "selected_pmids": [p["pmid"] for p in papers],
                 "skipped_pmids": [p["pmid"] for p in data.get("skipped", [])],
                 "strict_holdout_pmids": data.get("strict_holdout_pmids", [p["pmid"] for p in papers])}
    (folder / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    cases = [{"nct_id": nct_id, "pmid": p["pmid"], "title": p["title"], "abstract": p["abstract"],
              "abstract_sha256": sha(p["abstract"]), "paper_raw_hash": p["raw_hash"],
              "registry_hash": data["snapshot"].content_hash,
              "strict_holdout": p["pmid"] in selection["strict_holdout_pmids"],
              "exposure_note": None if p["pmid"] in selection["strict_holdout_pmids"] else "Prior project exposure; production coverage only"} for p in papers]
    (folder / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    return len(cases)


def prepare(conn, folder=FOLDER):
    folder.mkdir(parents=True, exist_ok=False)
    # Commit the rule to disk before constructing and drawing the pool.
    (folder / "RULE.md").write_text(RULE, encoding="utf-8")
    pool, excluded_pmids, excluded_texts = pool_from_database(conn)
    selected, reserve = choose_trials(pool)
    if not selected:
        raise ValueError("No clean unseen trials remain; ingest new cases rather than reuse development data.")
    counts = {}
    for nct_id in selected:
        counts[nct_id] = write_trial(folder / "trials" / nct_id, nct_id, pool[nct_id])
    # Reserve exact inputs now; no later metadata/content-based resampling is allowed.
    reserve_inputs = {}
    for n in reserve:
        d = pool[n]
        reserve_inputs[n] = {"snapshot": d["snapshot"].model_dump(mode="json"),
                             "safety": d["safety"].model_dump(mode="json"),
                             "papers": d["selected"], "skipped": d.get("skipped", []),
                             "strict_holdout_pmids": d["strict_holdout_pmids"]}
    (folder / "reserve_inputs.json").write_text(json.dumps(reserve_inputs, default=str), encoding="utf-8")
    (folder / "trials.txt").write_text("\n".join(selected) + "\n", encoding="utf-8")
    (folder / "draw.txt").write_text("\n".join(selected + reserve) + "\n", encoding="utf-8")
    manifest = {"prepared_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
                "pool_trials": len(pool), "pool_clean_abstracts": sum(len(d["papers"]) for d in pool.values()),
                "excluded_pmids": excluded_pmids, "excluded_abstract_fingerprints": excluded_texts,
                "selected_trials": len(selected), "selected_abstracts": sum(counts.values()), "per_trial": counts,
                "status": "awaiting blind human labels; not frozen; no model called"}
    manifest["source_hashes"] = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (folder / "trials").rglob("*")
                                 if p.is_file() and p.name in ("cases.jsonl", "safety.json", "sheet.md", "registry.md", "selection.json")}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "reference_review.json").write_text(json.dumps(
        {"reviewer": None, "completed": False, "reviewed_at": None}, indent=2), encoding="utf-8")
    sheet = "# Safety v2: blind human labeling sheets\n\nRead protocol.md first. No system outputs are included.\n\n"
    for n in selected:
        sheet += f"\n### Trial {n}\n\n" + (folder / "trials" / n / "sheet.md").read_text(encoding="utf-8")
    (folder / "sheet.md").write_text(sheet, encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, default=FOLDER)
    args = parser.parse_args()
    with psycopg.connect(Settings().database_url) as conn:
        result = prepare(conn, args.folder)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
