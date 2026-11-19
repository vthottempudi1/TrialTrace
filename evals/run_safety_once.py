"""Run the frozen pool and challenge once, journaling every API attempt.

No model-version probe and no refresh: 154 pool abstracts plus 10 challenge cases.
The existing readers, comparison code and scorers supply all evaluated behavior.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
from openai import OpenAI

from trialtrace.config import Settings
from trialtrace.safety.read_safety import read_safety_for_trial
from trialtrace.safety.safety_llm import read_safety_with_llm

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evals/safety"
RUNS = ROOT / "evals/runs"


def verify_freeze(manifest):
    for relative, expected in manifest["files"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Frozen file changed: {relative}")


def main():
    manifest = json.loads((FOLDER / "freeze.json").read_text(encoding="utf-8"))
    verify_freeze(manifest)
    settings = Settings()
    assert settings.paper_reader_model == manifest["reader"]["model"]
    assert settings.paper_reader_temperature == manifest["reader"]["temperature"]
    assert settings.openai_api_key and settings.database_url
    run_id = manifest["run_id"]
    RUNS.mkdir(exist_ok=True)
    journal_path = RUNS / f"{run_id}-api.jsonl"
    # Exclusive creation prevents accidental repetition of this evaluation.
    journal = journal_path.open("x", encoding="utf-8")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(),
                    timeout=settings.llm_timeout_seconds, max_retries=0)
    context = {"phase": "pool", "trial": None}
    attempts = []

    def create(**request):
        record = {**context, "at": datetime.now(timezone.utc).isoformat(),
                  "prompt_sha256": hashlib.sha256(request["messages"][0]["content"].encode()).hexdigest()}
        # Write before sending so even an interrupted request is visible.
        journal.write(json.dumps({**record, "event": "request"}) + "\n")
        journal.flush()
        try:
            response = client.chat.completions.create(**request)
        except Exception as error:
            journal.write(json.dumps({**record, "event": "error", "error_type": type(error).__name__}) + "\n")
            journal.flush()
            raise
        item = {**record, "event": "response", "response": response.model_dump(mode="json")}
        journal.write(json.dumps(item) + "\n")
        journal.flush()
        attempts.append(item)
        return response

    audited_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    progress_path = RUNS / f"{run_id}-progress.jsonl"
    with progress_path.open("x", encoding="utf-8") as progress:
        with psycopg.connect(settings.database_url) as conn:
            assert conn.execute("SELECT count(*) FROM safety_readings WHERE nct_id = ANY(%s)",
                                (manifest["trials"],)).fetchone()[0] == 0
            for index, nct_id in enumerate(manifest["trials"], 1):
                context["trial"] = nct_id
                summary = read_safety_for_trial(conn, settings, nct_id, run_id, client=audited_client)
                conn.commit()
                assert summary.status == "done" and summary.papers_skipped == 0
                progress.write(json.dumps(summary.model_dump()) + "\n")
                progress.flush()
                print(f"{index}/53 {nct_id}: calls={summary.llm_calls}, reused={summary.llm_reused}, "
                      f"failed={summary.llm_failed}, accepted={summary.model_statements}, rejected={summary.model_rejected}", flush=True)
    verify_freeze(manifest)
    # The pool scorer reads the stored first-pass answers; it makes no API calls.
    import evals.run_safety as pool
    sys.argv = ["run_safety", "--name", run_id]
    pool.main()
    # Run the existing challenge scorer with the same audited API client.
    import evals.run_safety_challenge as challenge
    context.update(phase="challenge", trial=None)
    def read_challenge(paper, reader):
        context["case"] = paper.pmid
        return read_safety_with_llm(paper, reader, client=audited_client)
    challenge.read_safety_with_llm = read_challenge
    sys.argv = ["run_safety_challenge", "--name", run_id]
    challenge.main()
    verify_freeze(manifest)
    journal.close()
    receipt = {"run_id": run_id, "freeze_sha256": hashlib.sha256((FOLDER / "freeze.json").read_bytes()).hexdigest(),
               "completed_at": datetime.now(timezone.utc).isoformat(), "responses": len(attempts),
               "responses_by_phase": {phase: sum(r["phase"] == phase for r in attempts) for phase in ("pool", "challenge")},
               "served_models": sorted({r["response"]["model"] for r in attempts}),
               "input_tokens": sum((r["response"].get("usage") or {}).get("prompt_tokens", 0) for r in attempts),
               "output_tokens": sum((r["response"].get("usage") or {}).get("completion_tokens", 0) for r in attempts)}
    (RUNS / f"{run_id}-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("Completed frozen run:", json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
