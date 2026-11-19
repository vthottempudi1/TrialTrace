"""Ingest the first 80 not-yet-stored trials from the saved v5 reserve order. No model calls."""
import json
import sys
from pathlib import Path

import httpx
import psycopg

from trialtrace.config import Settings
from trialtrace.db.repository import list_trial_ids
from trialtrace.ingest_papers import ingest_papers
from trialtrace.ingest_registry import ingest_trial

FOLDER = Path("evals/safety_v6_acquisition")
COUNT = 80


def main():
    settings = Settings()
    reserve = json.loads(Path("evals/safety_v5_acquisition/draw.json").read_text(encoding="utf-8"))["reserve"]
    draw_file = FOLDER / "draw.json"
    with psycopg.connect(settings.database_url) as conn, httpx.Client(timeout=30) as http:
        if not draw_file.exists():
            stored = set(list_trial_ids(conn))
            selected = [n for n in reserve if n not in stored][:COUNT]
            draw_file.write_text(json.dumps({"source": "evals/safety_v5_acquisition/draw.json reserve",
                                             "stored_before": len(stored), "selected": selected}, indent=2), encoding="utf-8")
        draw = json.loads(draw_file.read_text(encoding="utf-8"))
        if len(sys.argv) > 2 and sys.argv[1] == "--more":
            stored = set(list_trial_ids(conn))
            more = [n for n in reserve if n not in stored and n not in draw["selected"]][:int(sys.argv[2])]
            draw["selected"] += more
            draw["more"] = draw.get("more", []) + more
            draw_file.write_text(json.dumps(draw, indent=2), encoding="utf-8")
        selected = draw["selected"]
        receipt_path = FOLDER / "receipts.jsonl"
        receipts = [json.loads(s) for s in receipt_path.read_text().splitlines()] if receipt_path.exists() else []
        done = {r["nct_id"] for r in receipts}
        for nct in selected:
            if nct in done:
                continue
            registry = ingest_trial(conn, http, settings, nct)
            conn.commit()
            papers = ingest_papers(conn, http, settings, nct) if registry.status in ("new_version", "unchanged") else None
            conn.commit()
            result = {"nct_id": nct, "registry": registry.model_dump(mode="json"),
                      "papers": papers.model_dump(mode="json") if papers else None}
            with receipt_path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(result) + "\n")
            print(nct, registry.status, papers.status if papers else "not searched", flush=True)


if __name__ == "__main__":
    main()
