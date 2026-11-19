"""Add numeric branch coverage before any reference labels or model outputs."""
import hashlib
import json
from pathlib import Path

from evals.make_safety_cases import blind_sheet
from evals.prepare_safety_v4 import FOLDER, challenge_cases, registry_text, write_json


def main():
    if (FOLDER / "freeze.json").exists() or (FOLDER / "model_run_started.json").exists():
        raise ValueError("Cannot amend after freeze or model output")
    for path in FOLDER.rglob("references.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if json.loads(line)["statements"] is not None:
                raise ValueError("This amendment must precede reference labeling")
    index = (FOLDER / "REVIEW_INDEX.md").read_text(encoding="utf-8")
    added = []
    for case, safety in challenge_cases():
        cid = case["case_id"]
        folder = FOLDER / "targeted" / cid
        if folder.exists():
            if json.loads((folder / "cases.json").read_text())[0] != case:
                raise ValueError("Original constructed case changed")
            continue
        folder.mkdir()
        write_json(folder / "cases.json", [case])
        write_json(folder / "safety.json", safety.model_dump(mode="json"))
        (folder / "sheet.md").write_text(blind_sheet(case["nct_id"], [case]), encoding="utf-8")
        (folder / "registry.md").write_text("# CONSTRUCTED — not a real registry record\n\n" + registry_text(case["nct_id"], safety), encoding="utf-8")
        (folder / "references.jsonl").write_text(json.dumps({"case_id": cid, "document": None,
            "statements": None, "notes": ""}) + "\n", encoding="utf-8")
        index += f"| Constructed {cid} | 1 | [read](targeted/{cid}/sheet.md) | [read](targeted/{cid}/registry.md) | [fill](targeted/{cid}/references.jsonl) |\n"
        added.append(cid)
    (FOLDER / "REVIEW_INDEX.md").write_text(index, encoding="utf-8")
    (FOLDER / "protocol.md").write_text(Path("evals/safety_v4_protocol.md").read_text(encoding="utf-8"), encoding="utf-8")
    with (FOLDER / "RULE.md").open("a", encoding="utf-8") as out:
        out.write("\nPre-label coverage amendment: append six agreement and six disagreement cases (40 constructed total, ten per numeric branch). The original 28 sources and naturalistic draw are unchanged. No reference was labelled and no model was called.\n")
    manifest = json.loads((FOLDER / "manifest.json").read_text())
    manifest["targeted_cases"] = 40
    for path in (FOLDER / "targeted").rglob("*"):
        if path.is_file() and path.name in ("cases.json", "safety.json", "sheet.md", "registry.md"):
            manifest["input_hashes"][str(path.relative_to(FOLDER))] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(FOLDER / "manifest.json", manifest)
    write_json(FOLDER / "challenge_amendment.json", {"added_case_ids": added,
        "reason": "Reach ten agreeing and ten differing numeric references before labels or model output",
        "reference_labels_present": False, "model_calls": 0})
    print("Added", len(added), "constructed cases; no original sources changed.")


if __name__ == "__main__":
    main()
