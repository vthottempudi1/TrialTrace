"""Finish only the unstarted challenge after the documented PMID-format fix."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from openai import OpenAI
from trialtrace.config import Settings
from trialtrace.safety.safety_llm import read_safety_with_llm
from evals.run_safety_once import ROOT, FOLDER, RUNS, verify_freeze


def main():
    manifest = json.loads((FOLDER / "freeze.json").read_text())
    amendment = json.loads((FOLDER / "freeze_amendment_1.json").read_text())
    assert hashlib.sha256((FOLDER / "freeze.json").read_bytes()).hexdigest() == amendment["original_manifest_sha256"]
    # Preserve the original freeze; apply only the separately documented file overrides.
    manifest["files"].update(amendment["files"])
    verify_freeze(manifest)
    run_id = manifest["run_id"]
    journal_path = RUNS / f"{run_id}-api.jsonl"
    previous = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert sum(r["event"] == "request" for r in previous) == 154
    assert sum(r["event"] == "response" for r in previous) == 154
    assert not any(r.get("phase") == "challenge" or r["event"] == "error" for r in previous)
    start_path = RUNS / f"{run_id}-challenge-started.json"
    with start_path.open("x", encoding="utf-8") as out:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat(),
                   "amendment_sha256": hashlib.sha256((FOLDER / "freeze_amendment_1.json").read_bytes()).hexdigest()}, out)
    settings = Settings()
    assert settings.paper_reader_model == manifest["reader"]["model"]
    assert settings.paper_reader_temperature == manifest["reader"]["temperature"]
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.llm_timeout_seconds, max_retries=0)
    context = {}
    with journal_path.open("a", encoding="utf-8") as journal:
        def create(**request):
            record = {"phase": "challenge", **context, "at": datetime.now(timezone.utc).isoformat(),
                      "prompt_sha256": hashlib.sha256(request["messages"][0]["content"].encode()).hexdigest()}
            journal.write(json.dumps({**record, "event": "request"}) + "\n")
            journal.flush()
            try:
                response = client.chat.completions.create(**request)
            except Exception as error:
                journal.write(json.dumps({**record, "event": "error", "error_type": type(error).__name__}) + "\n")
                journal.flush()
                raise
            journal.write(json.dumps({**record, "event": "response", "response": response.model_dump(mode="json")}) + "\n")
            journal.flush()
            return response
        audited = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        import evals.run_safety_challenge as challenge
        def read(paper, reader):
            context["case"] = f"ch{int(paper.pmid) - 900000000:02}"
            return read_safety_with_llm(paper, reader, client=audited)
        challenge.read_safety_with_llm = read
        sys.argv = ["run_safety_challenge", "--name", run_id]
        challenge.main()
    verify_freeze(manifest)
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    responses = [r for r in rows if r["event"] == "response"]
    receipt = {"run_id": run_id, "freeze_sha256": amendment["original_manifest_sha256"],
               "amendment_sha256": hashlib.sha256((FOLDER / "freeze_amendment_1.json").read_bytes()).hexdigest(),
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "requests": sum(r["event"] == "request" for r in rows), "responses": len(responses),
               "errors": sum(r["event"] == "error" for r in rows),
               "responses_by_phase": {phase: sum(r["phase"] == phase for r in responses) for phase in ("pool", "challenge")},
               "served_models": sorted({r["response"]["model"] for r in responses}),
               "input_tokens": sum((r["response"].get("usage") or {}).get("prompt_tokens", 0) for r in responses),
               "output_tokens": sum((r["response"].get("usage") or {}).get("completion_tokens", 0) for r in responses)}
    (RUNS / f"{run_id}-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("Completed frozen evaluation:", json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
