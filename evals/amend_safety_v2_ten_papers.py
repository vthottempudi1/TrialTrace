"""Replace the unlabelled three-paper sample with exact production selection, without redrawing trials."""

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals.prepare_safety_v2 import FOLDER, RULE, pool_from_database, write_trial
from trialtrace.config import Settings
from trialtrace.match_papers import choose_papers


def require_unlabelled(folder):
    if (folder / "freeze.json").exists() or (folder / "model_run_started.json").exists():
        raise ValueError("Cannot amend a frozen or started evaluation")
    for name in ("labels.txt", "match_labels.jsonl"):
        for path in (folder / "trials").glob(f"*/{name}"):
            if any(line.strip() and not line.startswith("#") for line in path.read_text(encoding="utf-8").splitlines()):
                raise ValueError(f"Reference work already exists in {path}; preserve and reconcile it before amending")


def amend(conn, folder=FOLDER):
    require_unlabelled(folder)
    target = folder / "amendments" / "ten_paper_selection.json"
    if target.exists():
        raise ValueError("The ten-paper amendment is already recorded")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["source_hashes"].items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Original case source changed: {name}")
    selected = (folder / "trials.txt").read_text(encoding="utf-8").split()
    order = (folder / "draw.txt").read_text(encoding="utf-8").split()
    pool, _, _ = pool_from_database(conn)
    if any(n not in pool for n in order):
        raise ValueError("A selected/reserve trial is no longer eligible; do not silently redraw")
    for n in selected:
        picked, _ = choose_papers(conn, n, 10)
        if [(p["pmid"], p["raw_hash"]) for p in picked] != [(p["pmid"], p["raw_hash"]) for p in pool[n]["selected"]]:
            raise ValueError("Evaluation selection differs from production")
        old = [json.loads(line) for line in (folder / "trials" / n / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
        if old and old[0]["registry_hash"] != pool[n]["snapshot"].content_hash:
            raise ValueError("Registry version changed; this amendment only changes paper selection")
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / "before_ten_paper_selection.zip"
    existing = [p for p in folder.rglob("*") if p.is_file() and "amendments" not in p.relative_to(folder).parts]
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as out:
        for path in existing:
            out.write(path, path.relative_to(folder).as_posix())
    record = {"written_at": datetime.now(timezone.utc).isoformat(),
              "reason": "Owner requested the production 10-paper cap and production ordering before labeling or model calls.",
              "prior_cases": manifest["selected_abstracts"], "paper_limit": 10, "trial_redraw": False,
              "selected_trials": selected, "source_archive": archive.name,
              "selection": "shared production selector: listed abstracts, newest version per PMID, numeric PMID ascending"}
    # Save the reason before changing the case sources.
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (folder / "RULE.md").write_text(RULE + "\nAmendment: amendments/ten_paper_selection.json. The prior three-paper draw is archived; trial order is unchanged.\n", encoding="utf-8")
    counts = {n: write_trial(folder / "trials" / n, n, pool[n], replace_sources=True) for n in selected}
    reserve = {n: {"snapshot": pool[n]["snapshot"].model_dump(mode="json"),
                   "safety": pool[n]["safety"].model_dump(mode="json"), "papers": pool[n]["selected"],
                   "skipped": pool[n]["skipped"], "strict_holdout_pmids": pool[n]["strict_holdout_pmids"]}
               for n in order if n not in selected}
    (folder / "reserve_inputs.json").write_text(json.dumps(reserve, default=str), encoding="utf-8")
    exposed = {n: [p["pmid"] for p in pool[n]["selected"] if p["pmid"] not in pool[n]["strict_holdout_pmids"]]
               for n in selected}
    manifest.update(selected_abstracts=sum(counts.values()), per_trial=counts, paper_limit=10,
                    skipped_abstracts=sum(len(pool[n]["skipped"]) for n in selected),
                    strict_holdout_abstracts=sum(counts[n] - len(exposed[n]) for n in selected),
                    coverage_only_pmids={n: ids for n, ids in exposed.items() if ids},
                    fully_unseen_trials=sum(not exposed[n] for n in selected), amendment=record)
    manifest["source_hashes"] = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (folder / "trials").rglob("*") if p.is_file()
                                 and p.name in ("cases.jsonl", "sheet.md", "safety.json", "registry.md", "selection.json")}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sheet = "# Safety v2: blind human labeling sheets (production 10-paper selection)\n\nRead protocol.md. No system outputs.\n\n"
    for n in selected:
        sheet += f"\n### Trial {n}\n\n" + (folder / "trials" / n / "sheet.md").read_text(encoding="utf-8")
    (folder / "sheet.md").write_text(sheet, encoding="utf-8")
    return {key: manifest[key] for key in ("selected_trials", "selected_abstracts", "paper_limit",
            "skipped_abstracts", "strict_holdout_abstracts", "fully_unseen_trials", "coverage_only_pmids")}


if __name__ == "__main__":
    with psycopg.connect(Settings().database_url) as connection:
        print(json.dumps(amend(connection), indent=2))
