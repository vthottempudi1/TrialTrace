"""Validate human references, freeze, then run the unseen safety workflow once.

No model calls are possible without complete references and an explicit --run.
The LLM helper is evaluated as a proposal; no human decisions are fabricated.
"""

import argparse
import hashlib
import json
import importlib.metadata
import platform
import math
import re
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals.run_safety import rate_text
from trialtrace.safety.safety_baseline import safety_sentence_spans
from trialtrace.safety.safety_llm import ModelSafetyStatement, read_safety_with_llm, safety_reader_info
from trialtrace.config import Settings
from trialtrace.contracts import SafetyExtraction
from trialtrace.paper_reader import reader_settings
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.safety.safety_assist import (
    comparison_for_report, fingerprint, prepare_packet, source_context, suggest_packet,
)

FOLDER = Path("evals/safety_v2")
ROOT = Path(__file__).resolve().parents[1]
STATES = {"same", "different", "unknown"}
HIGH_REVIEW = {"safety_unclear", "safety_not_comparable", "safety_in_paper_not_registry", "unknown"}
DIFFERENCES = {"safety_number_differs", "safety_in_paper_not_registry"}


def parse_labels(path):
    """Read independent human labels, including a genuine unknown-data category."""
    labels, empty = [], []
    allowed = {"safety_consistent", "safety_number_differs", "safety_not_comparable",
               "safety_in_paper_not_registry", "safety_unclear", "unknown"}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = [part.strip() for part in line.split(" | ")]
        if len(fields) == 2 and fields[1] == "none":
            empty.append(fields[0])
            continue
        if len(fields) != 11:
            raise ValueError(f"{path}:{number}: expected 11 fields or PMID | none")
        pmid, sentence, kind, term, group, count, denominator, percent, grade, related, category = fields
        if kind not in ("death", "serious", "event") or related not in ("y", "n") or category not in allowed:
            raise ValueError(f"{path}:{number}: invalid kind, related flag or category")
        labels.append({"pmid": pmid, "sentence": int(sentence), "kind": kind,
                       "term": None if term == "-" else term, "group": None if group == "-" else group,
                       "count": None if count == "-" else float(count),
                       "denominator": None if denominator == "-" else float(denominator),
                       "percent": None if percent == "-" else float(percent),
                       "grade": None if grade == "-" else grade, "related": related == "y", "category": category})
    return labels, empty


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def text_key(text):
    """Use explicit prelisted aliases, not broad shared-word event matching."""
    return " ".join((text or "").casefold().split())


def references(folder, require_review=False):
    trials = (folder / "trials.txt").read_text(encoding="utf-8").split()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.get("source_hashes", {}).items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Prepared source changed: {name}")
    if len(trials) != len(set(trials)) or not all(re.fullmatch(r"NCT[0-9]{8}", n) for n in trials):
        raise ValueError("Trial list contains invalid or duplicate IDs")
    result, total = {}, 0
    for nct_id in trials:
        directory = folder / "trials" / nct_id
        cases = read_jsonl(directory / "cases.jsonl")
        selection_path = directory / "selection.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else {
            "selected_pmids": [case["pmid"] for case in cases], "skipped_pmids": []}
        if selection["selected_pmids"] != [case["pmid"] for case in cases] or len(cases) > 10:
            raise ValueError(f"{nct_id}: cases do not match the recorded production selection")
        labels, empty = parse_labels(directory / "labels.txt")
        mappings = read_jsonl(directory / "match_labels.jsonl")
        pmids = {case["pmid"] for case in cases}
        if not cases or len(pmids) != len(cases) or any(c["nct_id"] != nct_id or hashlib.sha256(
            c["abstract"].encode("utf-8")).hexdigest() != c["abstract_sha256"] for c in cases):
            raise ValueError(f"{nct_id}: invalid or changed case inputs")
        if {label["pmid"] for label in labels} | set(empty) != pmids:
            raise ValueError(f"{nct_id}: every abstract needs statements or one 'none' line")
        if len(empty) != len(set(empty)) or set(empty) & {label["pmid"] for label in labels}:
            raise ValueError(f"{nct_id}: duplicated or contradictory 'none' labels")
        if len(mappings) != len(labels):
            raise ValueError(f"{nct_id}: one match_labels.jsonl entry is required per statement")
        safety = SafetyExtraction.model_validate_json((directory / "safety.json").read_text(encoding="utf-8"))
        arm_ids = {f"arm:{arm.group_id}" for arm in safety.arms}
        event_ids = {f"event:{event.kind}:{event.position}" for event in safety.events}
        for index, (label, mapping) in enumerate(zip(labels, mappings), 1):
            case = next(case for case in cases if case["pmid"] == label["pmid"])
            if not 1 <= label["sentence"] <= len(safety_sentence_spans(case["abstract"])):
                raise ValueError(f"{nct_id}: sentence outside the abstract")
            if mapping.get("label_index") != index:
                raise ValueError(f"{nct_id}: label_index must follow statement file order, starting at 1")
            for key, known in (("acceptable_arm_ids", arm_ids), ("acceptable_event_ids", event_ids)):
                values = mapping.get(key)
                if not isinstance(values, list) or not all(isinstance(v, str) and v in known for v in values):
                    raise ValueError(f"{nct_id}: invalid {key}; use an empty list when no candidate is equivalent")
            if label["kind"] != "event" and mapping["acceptable_event_ids"]:
                raise ValueError(f"{nct_id}: a death/serious total has no event-row alias")
            if mapping.get("window") not in STATES or mapping.get("population") not in STATES:
                raise ValueError(f"{nct_id}: window and population must explicitly be same/different/unknown")
            for key in ("event_aliases", "arm_aliases"):
                if not isinstance(mapping.get(key), list) or not all(isinstance(v, str) and v.strip() for v in mapping[key]):
                    raise ValueError(f"{nct_id}: {key} must be an explicit list of accepted names, possibly empty")
            if mapping["window"] != "unknown":
                abstract_quote, registry_quote = mapping.get("abstract_window_quote"), mapping.get("registry_window_quote")
                if not isinstance(abstract_quote, str) or not abstract_quote.strip() or abstract_quote not in case["abstract"]:
                    raise ValueError(f"{nct_id}: a known window requires an exact abstract quote")
                if not isinstance(registry_quote, str) or not registry_quote.strip() or registry_quote not in (safety.time_frame or ""):
                    raise ValueError(f"{nct_id}: a known window requires an exact registry-window quote")
            if label["kind"] == "event" and not label["term"]:
                raise ValueError(f"{nct_id}: a named event needs its name")
            if label["category"] in ("safety_consistent", "safety_number_differs"):
                if mapping["window"] != "same" or mapping["population"] != "same" or label["denominator"] is None:
                    raise ValueError(f"{nct_id}: numeric verdicts require verified scope and an explicit denominator")
            for key in ("count", "denominator", "percent"):
                value = label[key]
                if value is not None and (not math.isfinite(value) or value < 0 or (key != "percent" and value != int(value))):
                    raise ValueError(f"{nct_id}: invalid participant number")
            if label["percent"] is not None and label["percent"] > 100:
                raise ValueError(f"{nct_id}: percent above 100")
            if label["count"] is None and label["percent"] is None:
                raise ValueError(f"{nct_id}: a statement needs an explicit number (zero for none)")
        total += len(labels)
        result[nct_id] = {"cases": cases, "labels": labels, "empty": empty, "mappings": mappings, "selection": selection,
                          "safety": safety}
    review = json.loads((folder / "reference_review.json").read_text(encoding="utf-8"))
    if require_review and (review.get("completed") is not True or not str(review.get("reviewer") or "").strip()
                           or not review.get("reviewed_at")):
        raise ValueError("Complete reference_review.json with the actual independent human review first")
    return result, total


def matches(label, mapping, statement):
    if statement.kind != label["kind"] or statement.sentence != label["sentence"]:
        return False
    if label["kind"] == "event":
        aliases = [label["term"]] + mapping["event_aliases"]
        if text_key(statement.term) not in {text_key(a) for a in aliases}:
            return False
    return (statement.count == label["count"] if label["count"] is not None
            else statement.percent == label["percent"])


def pair_labels(labels, mappings, statements):
    used, pairs = set(), {}
    for li, (label, mapping) in enumerate(zip(labels, mappings)):
        for si, statement in enumerate(statements):
            if si not in used and matches(label, mapping, statement):
                used.add(si)
                pairs[li] = si
                break
    return pairs


def source_hashes(folder):
    paths = list(Path("trialtrace").rglob("*.py"))
    paths += [Path("evals/run_safety_v2.py"), Path("evals/prepare_safety_v2.py"), Path("evals/run_safety.py")]
    paths += [p for p in folder.rglob("*") if p.is_file() and p.name not in ("freeze.json", "freeze.md")]
    return {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def assert_still_unseen(data, conn=None):
    """Catch product-model exposure that occurred after the blind set was prepared."""
    pmids = {case["pmid"] for trial in data.values() for case in trial["cases"]}
    texts = {case["abstract_sha256"] for trial in data.values() for case in trial["cases"]}
    connection = nullcontext(conn) if conn is not None else psycopg.connect(Settings().database_url)
    with connection as database:
        for table in ("paper_matches", "safety_readings"):
            rows = database.execute(
                f"SELECT DISTINCT r.pmid, p.abstract FROM {table} r JOIN papers p "
                "ON p.pmid=r.pmid AND p.raw_hash=r.paper_raw_hash WHERE r.system='llm'")
            for pmid, abstract in rows:
                if pmid in pmids or (abstract and hashlib.sha256(abstract.encode()).hexdigest() in texts):
                    raise ValueError(f"Holdout contamination: PMID {pmid} was model-read outside this evaluation")


def freeze(folder):
    data, total = references(folder, require_review=True)
    total = strict_statement_count(data)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if total < 60 and manifest["selected_trials"] < manifest["pool_trials"]:
        raise ValueError("The prewritten stopping rule requires extending by the next reserve trials first")
    assert_still_unseen(data)
    target = folder / "freeze.json"
    data = {"frozen_at": datetime.now(timezone.utc).isoformat(),
            "reader": safety_reader_info(reader_settings(Settings())), "files": source_hashes(folder),
            "labels": total, "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in ("openai", "pydantic", "pydantic-settings", "psycopg")},
            "scope": "LLM extraction and unapproved helper proposals; no full text or agents"}
    with target.open("x", encoding="utf-8") as out:
        json.dump(data, out, indent=2)
    (folder / "freeze.md").write_text(
        "# Safety v2 freeze\n\n" + data["frozen_at"] + "\n\nFull file hashes and configuration: freeze.json.\n"
        "Reference labels were reviewed before model output. No proposals are automatically human-approved.\n",
        encoding="utf-8")


def extend(folder):
    """Apply the preregistered label-count extension, without redrawing or touching existing labels."""
    from evals.prepare_safety_v2 import write_trial
    from trialtrace.contracts import Snapshot
    if (folder / "freeze.json").exists():
        raise ValueError("Cannot extend a frozen evaluation")
    current, total = references(folder)
    total = strict_statement_count(current)
    if total >= 60:
        raise ValueError("The prewritten statement-count stopping point has already been reached")
    selected = (folder / "trials.txt").read_text(encoding="utf-8").split()
    order = (folder / "draw.txt").read_text(encoding="utf-8").split()
    added = [n for n in order if n not in selected][:10]
    if not added:
        raise ValueError("The preselected pool is exhausted; report limited coverage")
    reserve = json.loads((folder / "reserve_inputs.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for nct_id in added:
        saved = reserve[nct_id]
        data = {"snapshot": Snapshot(**saved["snapshot"]), "safety": SafetyExtraction(**saved["safety"]),
                "selected": saved["papers"], "skipped": saved.get("skipped", []),
                "strict_holdout_pmids": saved.get("strict_holdout_pmids", [p["pmid"] for p in saved["papers"]])}
        manifest["per_trial"][nct_id] = write_trial(folder / "trials" / nct_id, nct_id, data)
    selected += added
    (folder / "trials.txt").write_text("\n".join(selected) + "\n", encoding="utf-8")
    manifest.setdefault("extensions", []).append({"at": datetime.now(timezone.utc).isoformat(),
                                                  "added_trials": added, "prior_statement_count": total})
    manifest["selected_trials"] = len(selected)
    manifest["selected_abstracts"] = sum(manifest["per_trial"].values())
    case_rows = {n: read_jsonl(folder / "trials" / n / "cases.jsonl") for n in selected}
    selections = {n: json.loads((folder / "trials" / n / "selection.json").read_text(encoding="utf-8"))
                  for n in selected if (folder / "trials" / n / "selection.json").exists()}
    manifest["paper_limit"] = 10
    manifest["strict_holdout_abstracts"] = sum(c.get("strict_holdout", True) for rows in case_rows.values() for c in rows)
    manifest["fully_unseen_trials"] = sum(all(c.get("strict_holdout", True) for c in rows) for rows in case_rows.values())
    manifest["skipped_abstracts"] = sum(len(s.get("skipped_pmids", [])) for s in selections.values())
    manifest["source_hashes"] = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (folder / "trials").rglob("*") if p.is_file()
                                 and p.name in ("cases.jsonl", "safety.json", "sheet.md", "registry.md", "selection.json")}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "reference_review.json").write_text(json.dumps(
        {"reviewer": None, "completed": False, "reviewed_at": None}, indent=2), encoding="utf-8")
    sheet = "# Safety v2: blind human labeling sheets\n\nNo system outputs are included.\n\n"
    for nct_id in selected:
        sheet += f"\n### Trial {nct_id}\n\n" + (folder / "trials" / nct_id / "sheet.md").read_text(encoding="utf-8")
    (folder / "sheet.md").write_text(sheet, encoding="utf-8")
    print("Added the next preselected trials:", ", ".join(added))


def strict_data(data):
    """Keep full production coverage separate from the uncontaminated case subset."""
    return {nct: {**trial, "cases": [case for case in trial["cases"] if case.get("strict_holdout", True)]}
            for nct, trial in data.items()}


def strict_statement_count(data):
    return sum(sum(label["pmid"] in {case["pmid"] for case in trial["cases"]
                                    if case.get("strict_holdout", True)} for label in trial["labels"])
               for trial in data.values())


def verify_freeze(folder):
    data = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    if data.get("python") != platform.python_version() or any(
        importlib.metadata.version(name) != expected for name, expected in data.get("packages", {}).items()
    ):
        raise ValueError("Python/dependency versions differ from the freeze")
    for name, expected in data["files"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen file changed: {name}")
    if data["reader"] != safety_reader_info(reader_settings(Settings())):
        raise ValueError("Reader settings differ from the freeze")
    return data


def score(data, outputs):
    counts = {"labels": 0, "statements": 0, "matched": 0, "scored_fields_right": 0,
              "category_right": 0, "false_consistent": 0, "consistent_predictions": 0,
              "differences": 0, "differences_routed": 0, "rejected": 0, "failed": 0,
              "helper_calls": 0, "helper_failed": 0, "helper_proposals": 0, "helper_correct": 0,
              "empty_abstracts": 0, "empty_right": 0, "verified_comparisons": 0,
              "helper_window_claims": 0, "helper_window_correct": 0, "helper_abstained": 0,
              "branches": {}, "misses": []}
    for nct_id, gold in data.items():
        for case in gold["cases"]:
            result = outputs[f"{nct_id}:{case['pmid']}"]
            statements = [ModelSafetyStatement(**s) for s in result["reading"]["statements"]]
            li = [i for i, label in enumerate(gold["labels"]) if label["pmid"] == case["pmid"]]
            labels, mappings = [gold["labels"][i] for i in li], [gold["mappings"][i] for i in li]
            pairs = pair_labels(labels, mappings, statements)
            reverse = {si: i for i, si in pairs.items()}
            if case["pmid"] in gold["empty"]:
                counts["empty_abstracts"] += 1
                counts["empty_right"] += not statements and result["reading"]["status"] == "ok"
            counts["labels"] += len(labels)
            counts["statements"] += len(statements)
            counts["matched"] += len(pairs)
            counts["rejected"] += len(result["reading"]["rejected"])
            counts["failed"] += result["reading"]["status"] != "ok"
            for i, label in enumerate(labels):
                counts["branches"][label["category"]] = counts["branches"].get(label["category"], 0) + 1
                is_difference = label["category"] in DIFFERENCES
                counts["differences"] += is_difference
                if i not in pairs:
                    counts["misses"].append({"nct_id": nct_id, "label": label})
                    continue
                si = pairs[i]
                st, comparison, mapping = statements[si], result["comparisons"][si], mappings[i]
                arm_aliases = [label["group"]] + mapping["arm_aliases"]
                fields_right = (text_key(st.arm) in {text_key(a) for a in arm_aliases}
                                and st.count == label["count"] and st.denominator == label["denominator"]
                                and st.percent == label["percent"] and text_key(st.grade) == text_key(label["grade"])
                                and st.treatment_related == label["related"])
                counts["scored_fields_right"] += fields_right
                counts["category_right"] += comparison["category"] == label["category"]
                counts["differences_routed"] += is_difference and comparison["category"] in HIGH_REVIEW
            for si, comparison in enumerate(result["comparisons"]):
                counts["verified_comparisons"] += comparison["category"] in ("safety_consistent", "safety_number_differs")
                if comparison["category"] == "safety_consistent":
                    counts["consistent_predictions"] += 1
                    counts["false_consistent"] += si not in reverse or labels[reverse[si]]["category"] != "safety_consistent"
                packet = result["helpers"].get(str(si))
                if packet is None:
                    continue
                counts["helper_calls"] += 1
                counts["helper_failed"] += packet["status"] == "failed"
                counts["helper_abstained"] += packet["status"] == "abstained"
                if packet["status"] not in ("ready", "abstained"):
                    continue
                mapping = mappings[reverse[si]] if si in reverse else None
                for field in ("arm", "event"):
                    proposed = packet["proposal"][field]["candidate_id"]
                    if proposed is not None:
                        counts["helper_proposals"] += 1
                        counts["helper_correct"] += bool(mapping and proposed in mapping[f"acceptable_{field}_ids"])
                window = packet["proposal"]["window"]["assessment"]
                if window != "unknown":
                    counts["helper_window_claims"] += 1
                    counts["helper_window_correct"] += bool(mapping and window == mapping["window"])
    counts["targets"] = {
        "extraction_recall_95_percent": "not tested" if not counts["labels"] else ("met" if counts["matched"] / counts["labels"] >= .95 else "not met"),
        "extraction_precision_95_percent": "not tested" if not counts["statements"] else ("met" if counts["matched"] / counts["statements"] >= .95 else "not met"),
        "six_scored_fields_accuracy_95_percent": "not tested" if not counts["matched"] else ("met" if counts["scored_fields_right"] / counts["matched"] >= .95 else "not met"),
        "window_assertion_accuracy_95_percent": "insufficient coverage" if counts["helper_window_claims"] < 10 else ("met" if counts["helper_window_correct"] / counts["helper_window_claims"] >= .95 else "not met"),
        "zero_wrong_match_proposals": "insufficient coverage" if counts["helper_proposals"] < 30 else ("met" if counts["helper_correct"] == counts["helper_proposals"] else "not met"),
        "zero_false_consistent": "not tested" if counts["consistent_predictions"] < 10 else ("met" if counts["false_consistent"] == 0 else "not met"),
        "all_differences_routed": "insufficient coverage" if counts["differences"] < 10 else ("met" if counts["differences"] == counts["differences_routed"] else "not met"),
        "human_resolution_accuracy_and_time": "not tested by this automated run"}
    return counts


def score_cohorts(data, outputs):
    clean = strict_data(data)
    result = score(clean, outputs)
    result["cohort"] = "strict unseen abstract/trial cases"
    result["production_coverage"] = score(data, outputs)
    result["paper_coverage"] = {nct: trial.get("selection", {}) for nct, trial in data.items()}
    result["per_trial"] = {nct: score({nct: trial}, outputs) for nct, trial in clean.items()}
    result["cohort_counts"] = {
        "production_cases": sum(len(trial["cases"]) for trial in data.values()),
        "strict_unseen_cases": sum(len(trial["cases"]) for trial in clean.values()),
        "fully_unseen_trials": sum(bool(trial["cases"]) and all(case.get("strict_holdout", True)
                                  for case in trial["cases"]) for trial in data.values()),
        "papers_skipped_at_cap": sum(len(trial.get("selection", {}).get("skipped_pmids", [])) for trial in data.values())}
    macro = {}
    for metric, denominator in (("recall", "labels"), ("precision", "statements")):
        values = [trial["matched"] / trial[denominator] for trial in result["per_trial"].values() if trial[denominator]]
        macro[metric] = {"trial_mean": sum(values) / len(values) if values else None, "trials_counted": len(values)}
    result["trial_macro"] = macro
    return result


def run(folder):
    verify_freeze(folder)
    data, _ = references(folder, require_review=True)
    assert_still_unseen(data)
    settings = Settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    marker = folder / "model_run_started.json"
    with marker.open("x", encoding="utf-8") as out:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat()}, out)
    directory = folder / "run"
    directory.mkdir(exist_ok=False)
    reader, outputs = reader_settings(settings), {}
    for nct_id, reference in data.items():
        safety = reference["safety"]
        for case in reference["cases"]:
            reading = read_safety_with_llm(Paper(pmid=case["pmid"], title=case["title"], abstract=case["abstract"]),
                                           reader, target_nct_id=nct_id)
            result = {"nct_id": nct_id, "pmid": case["pmid"], "reading": reading.model_dump(mode="json"),
                      "comparisons": [], "helpers": {}}
            path = directory / f"{nct_id}-{case['pmid']}.json"
            path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            for i, statement in enumerate(reading.statements):
                comparison = comparison_for_report(statement, safety)
                result["comparisons"].append(comparison.model_dump(mode="json"))
                if comparison.category == "safety_unclear":
                    context = source_context(nct_id, case["pmid"], case["paper_raw_hash"], case["abstract"], statement, safety)
                    result["helpers"][str(i)] = suggest_packet(prepare_packet(context, settings), settings)
                path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            outputs[f"{nct_id}:{case['pmid']}"] = result
            print(f"{nct_id} PMID {case['pmid']}: {reading.status}, {len(reading.statements)} statements", flush=True)
    verify_freeze(folder)
    assert_still_unseen(data)
    result = score_cohorts(data, outputs)
    calls = []
    for output in outputs.values():
        if output["reading"].get("llm"):
            calls.append(output["reading"]["llm"])
        calls.extend(packet["llm"] for packet in output["helpers"].values() if packet.get("llm"))
    result["usage"] = {"logical_llm_calls": len(calls), "transport_attempts": sum(c.get("attempts", 1) for c in calls),
                       "served_models": sorted({c["model"] for c in calls}),
                       "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
                       "output_tokens": sum(c.get("output_tokens") or 0 for c in calls)}
    (directory / "scores.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Safety v2 evaluation", "", "Unreviewed reports and helper proposals; no human approvals were simulated.", "",
             "Headline scores below use the strict unseen subset. Production-coverage and per-trial scores are separate in scores.json.",
             f"Coverage: {json.dumps(result['cohort_counts'])}",
             f"Equal-trial-weighted extraction scores: {json.dumps(result['trial_macro'])}", "",
             f"Extraction recall: {rate_text(result['matched'], result['labels'])}",
             f"Extraction precision: {rate_text(result['matched'], result['statements'])}",
             f"Six scored fields right: {rate_text(result['scored_fields_right'], result['matched'])}",
             f"Empty abstracts kept empty: {rate_text(result['empty_right'], result['empty_abstracts'])}",
             f"Verified report comparison coverage: {rate_text(result['verified_comparisons'], result['labels'])}",
             f"Unreviewed report category: {rate_text(result['category_right'], result['labels'])}",
             f"Match proposal precision: {rate_text(result['helper_correct'], result['helper_proposals'])}",
             f"Window assertion accuracy: {rate_text(result['helper_window_correct'], result['helper_window_claims'])}",
             f"Differences routed: {rate_text(result['differences_routed'], result['differences'])}", ""]
    lines += [f"- {key}: {value}" for key, value in result["targets"].items()]
    lines += ["", "Report coverage, abstentions, failures, branch counts and misses are in scores.json.",
              "Intervals are statement-level Wilson ranges and do not adjust for trial clustering.",
              "This automated evaluation cannot validate human review effectiveness or confer safety certification."]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, default=FOLDER)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-labels", action="store_true")
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--extend", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.check_labels:
        data, count = references(args.folder)
        print(f"Validated {len(data)} trials and {count} statements. No model called.")
    elif args.freeze:
        freeze(args.folder)
    elif args.extend:
        extend(args.folder)
    else:
        run(args.folder)


if __name__ == "__main__":
    main()
