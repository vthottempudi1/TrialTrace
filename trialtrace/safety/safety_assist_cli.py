"""Commands for preparing model suggestions and recording human match reviews."""

import json
from pathlib import Path

import psycopg

from trialtrace.safety.safety_compare import compare_statement
from trialtrace.safety.safety_rollup import statement_from_row
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_papers_for_trial, get_safety, get_safety_readings
from trialtrace.safety.safety_assist import prepare_packet, review_packet, source_context, suggest_packet, render_packet


def suggest_safety(nct_ids, limit=10, out_dir=None, prepare_only=False):
    """Read existing extractions, never rerun them. Reuse existing suggestion packets."""
    settings = Settings()
    if not nct_ids or limit < 1:
        raise SystemExit("Give trial IDs and a positive --limit.")
    if not settings.database_url:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not configured.")
    if not prepare_only and not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")
    directory = Path(out_dir) if out_dir else settings.safety_match_dir
    made = 0
    with psycopg.connect(settings.database_url) as conn:
        for nct_id in nct_ids:
            snapshot = get_latest_snapshot(conn, nct_id)
            safety = get_safety(conn, nct_id, snapshot.content_hash) if snapshot else None
            if safety is None or safety.status != "ok":
                print(f"{nct_id}: no registry safety data")
                continue
            papers = {(p["pmid"], p["raw_hash"]): p for p in get_papers_for_trial(conn, nct_id)}
            for reading in get_safety_readings(conn, nct_id):
                if reading["system"] != "llm" or reading["status"] != "ok":
                    continue
                paper = papers.get((reading["pmid"], reading["paper_raw_hash"]))
                if not paper or not paper["abstract"]:
                    continue
                for row in reading["statements"]:
                    if row["rejected_code"] is not None:
                        continue
                    statement = statement_from_row(row)
                    comparison = compare_statement(statement, safety)
                    if comparison.category not in ("safety_unclear", "safety_consistent", "safety_number_differs"):
                        continue
                    context = source_context(nct_id, reading["pmid"], reading["paper_raw_hash"],
                                             paper["abstract"], statement, safety)
                    packet = prepare_packet(context, settings)
                    folder = directory / nct_id
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{reading['pmid']}-{packet['input_hash']}.json"
                    if path.exists():
                        existing = json.loads(path.read_text(encoding="utf-8"))
                        if existing["status"] != "prepared" or prepare_only:
                            print(f"Reused {path} ({existing['status']})")
                            continue
                    else:
                        with path.open("x", encoding="utf-8") as out:
                            json.dump(packet, out, ensure_ascii=False, indent=2)
                    if not prepare_only:
                        lock = path.with_suffix(".lock")
                        try:
                            with lock.open("x", encoding="utf-8") as out:
                                out.write("Matching request started; inspect the packet before retrying an interrupted call.")
                        except FileExistsError:
                            print(f"Request already started for {path}; not calling the model again.")
                            continue
                        packet = suggest_packet(packet, settings)
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
                        temporary.replace(path)
                        lock.unlink()
                    path.with_suffix(".md").write_text(render_packet(packet), encoding="utf-8")
                    print(f"{path}: {packet['status']}; original reason={comparison.reason_code}")
                    made += 1
                    if made >= limit:
                        return


def review_safety_match(path, reviewer, decision, reason, window, population):
    review = review_packet(Path(path), reviewer, decision, reason, window, population)
    print(f"Recorded {review['decision']} by {review['reviewer']}; proposal and prior reviews retained.")
