"""The model-mode challenge path must construct valid Paper inputs before calling a reader."""
import json
from types import SimpleNamespace

from evals import run_safety_challenge as challenge


def test_model_mode_accepts_all_constructed_cases_without_real_api_calls(monkeypatch, tmp_path):
    seen = []

    def fake_read(paper, reader):
        seen.append(paper)
        return SimpleNamespace(llm=None, rejected=[], statements=[])

    monkeypatch.setattr(challenge, "read_safety_with_llm", fake_read)
    monkeypatch.setattr(challenge, "RUNS_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["run_safety_challenge"])
    challenge.main()
    assert len(seen) == 10
    assert len({paper.pmid for paper in seen}) == 10
    assert all(paper.pmid.isdigit() and len(paper.pmid) <= 9 for paper in seen)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert [case["id"] for case in saved["cases"]] == [f"ch{i:02}" for i in range(1, 11)]
