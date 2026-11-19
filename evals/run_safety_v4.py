"""Check sources/independent labels, extend, freeze, then run once. No oracle approvals."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from evals.prepare_safety_v4 import write_trial
from evals.safety_v4_reference import ReferenceCase, validate_case
from evals.score_safety_v4 import score
from evals.run_safety import rate_text
from evals.run_safety_v2 import assert_still_unseen
from trialtrace.safety.safety_identity import check_reading_identity
from trialtrace.safety.safety_llm import ModelSafetyStatement, read_safety_with_llm, safety_reader_info
from trialtrace.config import Settings
from trialtrace.contracts import SafetyExtraction, Snapshot
from trialtrace.paper_reader import reader_settings
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.safety.safety_assist import (comparison_for_report, source_context, prepare_packet,
                                     suggest_packet, load_report_comparison, HELPER_VERSION)

FOLDER = Path("evals/safety_v4")
PACKAGES = ("openai", "pydantic", "pydantic-settings", "psycopg")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def items(folder, require_labels=True, require_review=False, check_lock=True):
    manifest = read_json(folder / "manifest.json")
    for name, expected in manifest["input_hashes"].items():
        if digest(folder / name) != expected:
            raise ValueError(f"Prepared source changed: {name}")
    candidate = read_json(folder / "system_candidate.json")
    if check_lock and (candidate["reader"] != safety_reader_info(reader_settings(Settings()))
            or candidate["helper_version"] != HELPER_VERSION):
        raise ValueError("System changed after the pre-label candidate lock")
    for name, expected in (candidate["files"].items() if check_lock else []):
        if digest(Path(name)) != expected:
            raise ValueError(f"System changed after pre-label lock: {name}")
    data, seen = [], set()
    for cohort in ("naturalistic", "targeted"):
        for directory in sorted((folder / cohort).iterdir()):
            if not directory.is_dir():
                continue
            safety = SafetyExtraction(**read_json(directory / "safety.json"))
            cases = read_json(directory / "cases.json")
            if cohort == "naturalistic":
                selection = read_json(directory / "selection.json")
                if len(cases) > 10 or selection["selected_pmids"] != [c["pmid"] for c in cases]:
                    raise ValueError("Cases differ from the recorded production paper selection")
            references = {}
            if require_labels:
                records = [json.loads(line) for line in (directory / "references.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                if len({r["case_id"] for r in records}) != len(records):
                    raise ValueError("Duplicate reference case ID")
                references = {r["case_id"]: ReferenceCase(**r) for r in records}
                if set(references) != {c["case_id"] for c in cases}:
                    raise ValueError("Reference case coverage differs from prepared sources")
            for case in cases:
                if case["synthetic"] != (cohort == "targeted"):
                    raise ValueError("Constructed and naturalistic source provenance cannot be interchanged")
                if case["case_id"] in seen:
                    raise ValueError("Duplicate prepared case ID")
                seen.add(case["case_id"])
                if case["abstract_sha256"] != hashlib.sha256(case["abstract"].encode()).hexdigest():
                    raise ValueError("Abstract fingerprint differs")
                if any(arm.nct_id != case["nct_id"] or arm.content_hash != case["registry_hash"] for arm in safety.arms):
                    raise ValueError("Registry source identity/version differs")
                if require_labels:
                    validate_case(references[case["case_id"]], case, safety)
                data.append({"cohort": cohort, "case": case, "safety": safety,
                             "reference": references.get(case["case_id"])})
    natural = [i for i in data if i["cohort"] == "naturalistic"]
    targeted = [i for i in data if i["cohort"] == "targeted"]
    if len(natural) != manifest["naturalistic_cases"] or len(targeted) != manifest["targeted_cases"]:
        raise ValueError("Prepared cohort counts differ from manifest")
    if {i["case"]["nct_id"] for i in natural} != set(manifest["selected_trials"]):
        raise ValueError("Naturalistic trial IDs differ from the saved draw")
    blind = read_json(folder / "blind_subset.json")["case_ids"]
    strict_ids = {i["case"]["case_id"] for i in natural if i["case"]["strict_holdout"]}
    if len(blind) != 20 or len(set(blind)) != 20 or not set(blind) <= strict_ids:
        raise ValueError("Independent blind subset must contain 20 distinct strict cases")
    if require_review:
        review = read_json(folder / "reference_review.json")
        if (not review.get("completed") or not str(review.get("reviewer") or "").strip()
                or not review.get("reviewed_at") or not review.get("blind_subset_completed")
                or not (review.get("blind_subset_labelled_before_ai_drafts") or review.get("blind_order_waived_by_owner"))
                or not str(review.get("blind_subset_reviewer") or "").strip()
                or not review.get("blind_subset_reviewed_at")):
            raise ValueError("Actual independent blind labeling and full human review must be recorded first")
    return data


def strict(data):
    return [i for i in data if i["cohort"] == "naturalistic" and i["case"]["strict_holdout"]]


def target_count(data):
    return sum(st.attribution == "target" for i in strict(data) for st in i["reference"].statements)


def check_unseen(data):
    trials = {}
    for item in strict(data):
        trials.setdefault(item["case"]["nct_id"], {"cases": []})["cases"].append(item["case"])
    assert_still_unseen(trials)


def source_hashes(folder):
    paths = list(Path("trialtrace").rglob("*.py")) + list(Path("trialtrace/db/migrations").glob("*.sql"))
    paths += list(Path("evals").glob("*safety*.py"))
    paths += [p for p in folder.rglob("*") if p.is_file() and p.name not in ("freeze.json", "freeze.md")]
    return {str(p): digest(p) for p in paths}


def configuration():
    return {"reader": safety_reader_info(reader_settings(Settings())), "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in PACKAGES}}


def freeze(folder):
    if (folder / "freeze.json").exists() or (folder / "model_run_started.json").exists():
        raise ValueError("This evaluation is already frozen or started")
    data = items(folder, require_review=True)
    if target_count(data) < 60 and read_json(folder / "manifest.json")["reserve_trials"]:
        raise ValueError("Fewer than 60 strict target statements: apply the saved extension rule first")
    check_unseen(data)
    record = {"at": now(), "configuration": configuration(), "files": source_hashes(folder),
              "strict_target_statements": target_count(data), "review": read_json(folder / "reference_review.json")}
    with (folder / "freeze.json").open("x", encoding="utf-8") as out:
        json.dump(record, out, indent=2)
    (folder / "freeze.md").write_text("# Safety v4 freeze\n\n" + record["at"] + "\n\nSee freeze.json for hashes, actual review and configuration.\n", encoding="utf-8")


def verify_freeze(folder):
    frozen = read_json(folder / "freeze.json")
    if frozen["configuration"] != configuration():
        raise ValueError("Reader configuration or environment differs from freeze")
    for name, expected in frozen["files"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"Frozen file changed: {name}")


def extend(folder):
    if (folder / "freeze.json").exists() or (folder / "model_run_started.json").exists():
        raise ValueError("Cannot extend a frozen or started evaluation")
    data = items(folder)
    if target_count(data) >= 60:
        raise ValueError("The count-based stopping point was already reached")
    manifest = read_json(folder / "manifest.json")
    added = manifest["reserve_trials"][:10]
    if not added:
        raise ValueError("Pool exhausted; report the remaining coverage limitation")
    reserve = read_json(folder / "reserve_inputs.json")
    added_cases = []
    for nct in added:
        record = dict(reserve[nct])
        record["snapshot"] = Snapshot(**record["snapshot"])
        record["safety"] = SafetyExtraction(**record["safety"])
        added_cases.extend(write_trial(folder / "naturalistic" / nct, nct, record))
    manifest["selected_trials"] += added
    manifest["reserve_trials"] = manifest["reserve_trials"][len(added):]
    manifest["naturalistic_cases"] += len(added_cases)
    manifest["strict_unseen_cases"] += sum(c["strict_holdout"] for c in added_cases)
    for path in (folder / "naturalistic").rglob("*"):
        if path.is_file() and path.name in ("cases.json", "safety.json", "snapshot.json", "selection.json", "sheet.md", "registry.md"):
            manifest["input_hashes"][str(path.relative_to(folder))] = digest(path)
    manifest.setdefault("extensions", []).append({"at": now(), "trials": added, "strict_target_count_before": target_count(data)})
    write_json(folder / "manifest.json", manifest)
    review = read_json(folder / "reference_review.json")
    with (folder / "review_history.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"at": now(), "prior_review": review, "added_trials": added}) + "\n")
    review.update(completed=False, reviewer=None, reviewed_at=None)
    write_json(folder / "reference_review.json", review)
    with (folder / "REVIEW_INDEX.md").open("a", encoding="utf-8") as out:
        for nct in added:
            out.write(f"| Extension {nct} | {sum(c['nct_id']==nct for c in added_cases)} | [read](naturalistic/{nct}/sheet.md) | [read](naturalistic/{nct}/registry.md) | [fill](naturalistic/{nct}/references.jsonl) |\n")
    print("Added", len(added), "reserved trials; prior sources/references unchanged, full review reopened.")


def score_sets(data, outputs):
    production = [i for i in data if i["cohort"] == "naturalistic"]
    targeted = [i for i in data if i["cohort"] == "targeted"]
    result = {"naturalistic_strict": score(strict(data), outputs), "naturalistic_production": score(production, outputs),
            "targeted_constructed": score(targeted, outputs),
            "per_trial": {n: score([i for i in strict(data) if i["case"]["nct_id"] == n], outputs)
                          for n in sorted({i["case"]["nct_id"] for i in strict(data)})},
            "targeted_families": {f: score([i for i in targeted if i["case"]["family"] == f], outputs)
                                 for f in sorted({i["case"]["family"] for i in targeted})}}
    result["trial_macro"] = {}
    for name, denominator in (("recall", "target_labels"), ("precision", "accepted_target_predictions")):
        values = [s["target_matches"] / s[denominator] for s in result["per_trial"].values() if s[denominator]]
        result["trial_macro"][name] = {"mean": sum(values) / len(values) if values else None, "trials_counted": len(values)}
    result["naturalistic_strict"]["targets"]["minimum_reference_statements"] = "met" if target_count(data) >= 60 else "insufficient coverage; pool exhausted"
    return result


def write_report(path, result):
    text = "# Safety v4 evaluation\n\nSeparate cohorts; no gold approvals simulated. Source inventory coverage is descriptive.\n"
    for name in ("naturalistic_strict", "naturalistic_production", "targeted_constructed"):
        scored = result[name]
        text += f"\n## {name}\n\n"
        for title, a, b in (("Target recall", "target_matches", "target_labels"),
            ("Target precision", "target_matches", "accepted_target_predictions"),
            ("Source-support precision", "supported_source_predictions", "raw_source_predictions"),
            ("All-field agreement", "all_fields_correct", "target_matches"),
            ("Fully correct target recall", "all_fields_correct", "target_labels"),
            ("Fully correct target precision", "all_fields_correct", "accepted_target_predictions"),
            ("Helper ID precision", "helper_correct", "helper_proposals"),
            ("Supported windows", "helper_window_supported", "helper_window_claims")):
            text += f"- {title}: {rate_text(scored[a], scored[b])}\n"
        text += f"- Claim families: {scored['claim_families']}; verified comparisons: {scored['verified_comparisons']}\n"
        text += "\n" + "\n".join(f"- {k}: {v}" for k, v in scored["targets"].items()) + "\n"
    text += "\nStatement intervals do not adjust for trial clustering. Constructed cases do not establish real-world reliability. Human resolution effectiveness is not established by automated scores.\n"
    path.write_text(text, encoding="utf-8")


def run(folder):
    verify_freeze(folder)
    data = items(folder, require_review=True)
    check_unseen(data)
    settings = Settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    with (folder / "model_run_started.json").open("x", encoding="utf-8") as out:
        json.dump({"at": now()}, out)
    directory = folder / "run"
    directory.mkdir(exist_ok=False)
    outputs, calls = {}, []
    for item in data:
        case, safety = item["case"], item["safety"]
        paper = Paper(pmid=case["pmid"], title=case["title"], abstract=case["abstract"])
        raw = read_safety_with_llm(paper, reader_settings(settings), target_nct_id=case["nct_id"])
        reading = check_reading_identity(raw, case["abstract"], safety, case["registry_title"])
        record = {"case_id": case["case_id"], "source_reading": raw.model_dump(mode="json"),
                  "reading": reading.model_dump(mode="json"), "comparisons": [], "helpers": {}}
        path = directory / (case["case_id"] + ".json")
        write_json(path, record)
        if raw.llm:
            calls.append(raw.llm.model_dump(mode="json"))
        for index, statement in enumerate(reading.statements):
            comparison = comparison_for_report(statement, safety)
            record["comparisons"].append(comparison.model_dump(mode="json"))
            if comparison.category == "safety_unclear":
                context = source_context(case["nct_id"], case["pmid"], case["paper_raw_hash"], case["abstract"], statement, safety)
                packet = suggest_packet(prepare_packet(context, settings), settings)
                record["helpers"][str(index)] = packet
                packet_dir = directory / "helper_packets" / case["nct_id"]
                packet_dir.mkdir(parents=True, exist_ok=True)
                write_json(packet_dir / f"{case['case_id']}-{index}.json", packet)
                if packet.get("llm"):
                    calls.append(packet["llm"])
            write_json(path, record)
        outputs[case["case_id"]] = record
        print(case["case_id"], reading.status, len(reading.statements), "target statements", flush=True)
    verify_freeze(folder)
    check_unseen(data)
    result = score_sets(data, outputs)
    result["usage"] = {"logical_calls": len(calls), "transport_attempts": sum(c.get("attempts", 1) for c in calls),
        "served_models": sorted({c["model"] for c in calls}),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls)}
    write_json(directory / "scores.json", result)
    write_report(directory / "report.md", result)
    write_json(directory / "receipt.json", {"at": now(), "usage": result["usage"],
        "files": {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file()}})


def score_reviewed(folder):
    verify_freeze(folder)
    data = items(folder, require_review=True)
    directory = folder / "run"
    outputs = {}
    for item in data:
        case, safety = item["case"], item["safety"]
        output = read_json(directory / (case["case_id"] + ".json"))
        for index, st in enumerate(output["reading"]["statements"]):
            statement = ModelSafetyStatement(**st)
            context = source_context(case["nct_id"], case["pmid"], case["paper_raw_hash"], case["abstract"], statement, safety)
            output["comparisons"][index] = load_report_comparison(directory / "helper_packets", context).model_dump(mode="json")
        outputs[case["case_id"]] = output
    result = score_sets(data, outputs)
    reviews = list((directory / "helper_packets").rglob("*.reviews.jsonl"))
    result["actual_review_files"] = {str(p): digest(p) for p in reviews}
    result["scope"] = "After actual saved human reviews; not a new model run. Empty logs mean no approvals."
    write_json(directory / ("reviewed_scores-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"), result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, default=FOLDER)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("check-sources", "check-labels", "extend", "freeze", "run", "score-reviewed"):
        actions.add_argument("--" + action, action="store_true")
    args = parser.parse_args()
    if args.check_sources or args.check_labels:
        data = items(args.folder, require_labels=not args.check_sources)
        print("Validated", len(data), "cases;", "sources only" if args.check_sources else f"{target_count(data)} strict target statements", "; no model called")
    elif args.extend:
        extend(args.folder)
    elif args.freeze:
        freeze(args.folder)
    elif args.run:
        run(args.folder)
    else:
        score_reviewed(args.folder)


if __name__ == "__main__":
    main()
