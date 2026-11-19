##############################################################################
# evals/run_safety.py
#
# PURPOSE: Score the STORED safety readings (model and code baseline) of the evaluation trials against
#          the labels, by the rules in evals/safety/rubric.md. No model is called here.
#
# STEPS:
#   1. Read each trial's labels.txt (one line per labelled statement, or "PMID | none").
#   2. Read the trial's newest stored safety readings; the model's statements are compared with the
#      registry by the same code the report uses.
#   3. Match labels to system statements ONE TO ONE (same paper, kind and sentence; event names share
#      a word; main number equal). A system statement is used for at most one label.
#   4. Count: extraction (E1 to E5), comparison (C1 to C3), routing (R1), each with a Wilson 95% range.
#      A branch with no labelled case is printed as "not tested", never as passed.
#   5. Print the keep rule, and save everything to evals/runs/.
#
# USE:     python -m evals.run_safety --name safety-final            # all 53 trials, plus the two subsets
##############################################################################

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from trialtrace.safety.safety_compare import compare_statement
from trialtrace.safety.safety_rollup import SAFETY_HIGH_RISK, check_safety_summary, quantity_name, statement_from_row
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_safety, get_safety_readings

FOLDER = Path(__file__).parent / "safety"
RUNS_DIR = Path(__file__).parent / "runs"
CATEGORIES = ("safety_consistent", "safety_number_differs", "safety_not_comparable", "safety_in_paper_not_registry", "safety_unclear")
DIFFERENCES = ("safety_number_differs", "safety_in_paper_not_registry")       # what routing (R1) must not miss
NAME_FILLER = {"adverse", "event", "events", "effect", "effects", "of", "the", "and", "or", "a", "an", "in"}
FIELDS = ("group", "count", "denominator", "percent", "grade", "related")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def number_or_none(text: str) -> float | None:
    """'-' -> None, '12' -> 12.0, '41.6' -> 41.6."""
    return None if text == "-" else float(text)


def parse_labels(path: Path) -> tuple[list[dict], list[str]]:
    """(labelled statements in file order, PMIDs labelled 'none')."""
    labels, empty = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(" | ")]
        if len(parts) == 2 and parts[1] == "none":
            empty.append(parts[0])
            continue
        if len(parts) != 11:
            raise ValueError(f"{path}: a label line needs 11 fields: {line!r}")
        pmid, sentence, kind, term, group, count, denominator, percent, grade, related, category = parts
        if kind not in ("death", "serious", "event") or category not in CATEGORIES or related not in ("y", "n"):
            raise ValueError(f"{path}: bad kind, category or related in {line!r}")
        labels.append({
            "pmid": pmid, "sentence": int(sentence), "kind": kind, "term": None if term == "-" else term,
            "group": None if group == "-" else group, "count": number_or_none(count),
            "denominator": number_or_none(denominator), "percent": number_or_none(percent),
            "grade": None if grade == "-" else grade, "related": related == "y", "category": category,
        })
    return labels, empty


def names_share_a_word(first: str | None, second: str | None) -> bool:
    return bool((set(words(first)) - NAME_FILLER) & (set(words(second)) - NAME_FILLER))


def baseline_numbers(numbers: list[str]) -> tuple[list[float], list[float]]:
    """The baseline's numbers as (plain numbers, percents): ["6", "21%"] -> ([6.0], [21.0])."""
    plain, percents = [], []
    for text in numbers:
        value = float(text.rstrip("%").replace(",", ""))
        if text.endswith("%"):
            percents.append(value)
        else:
            plain.append(value)
    return plain, percents


def fits(label: dict, statement: dict) -> bool:
    """True if a system statement is the labelled statement (rubric section 4)."""
    if statement["pmid"] != label["pmid"] or statement["kind"] != label["kind"] or statement["sentence"] != label["sentence"]:
        return False
    if label["kind"] == "event" and not names_share_a_word(label["term"], statement["term"]):
        return False
    if statement["system"] == "baseline":
        plain, percents = baseline_numbers(statement["numbers"])
        if label["count"] is not None:
            return label["count"] in plain or (label["count"] == 0 and statement["negated"])
        return label["percent"] in percents
    if label["count"] is not None:
        return statement["count"] == label["count"]
    return statement["percent"] == label["percent"]


def match_one_to_one(labels: list[dict], statements: list[dict]) -> dict[int, int]:
    """{label index: statement index}. Labels in file order; each takes the first unused statement that fits."""
    used = set()
    pairs = {}
    for label_index, label in enumerate(labels):
        for statement_index, statement in enumerate(statements):
            if statement_index not in used and fits(label, statement):
                used.add(statement_index)
                pairs[label_index] = statement_index
                break
    return pairs


def field_is_right(field: str, label: dict, statement: dict) -> bool:
    if field == "group":
        return words(label["group"]) == words(statement["group"])
    if field == "grade":
        return words(label["grade"]) == words(statement["grade"])
    return label[field] == statement[field]


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """The Wilson 95% range of a proportion, or None when nothing was counted."""
    if total == 0:
        return None
    z = 1.96
    rate = successes / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - half), min(1.0, centre + half)


def rate_text(successes: int, total: int) -> str:
    """'22/26 (85%, 95% range 66-94%)', or 'not tested (0 cases)'."""
    if total == 0:
        return "not tested (0 cases)"
    low, high = wilson(successes, total)
    return f"{successes}/{total} ({100 * successes / total:.0f}%, 95% range {100 * low:.0f}-{100 * high:.0f}%)"


def system_statements(conn, nct_id: str) -> tuple[list[dict], dict]:
    """Every usable statement both systems stored for one trial (newest run), and counts of what was set aside."""
    snapshot = get_latest_snapshot(conn, nct_id)
    safety = get_safety(conn, nct_id, snapshot.content_hash)
    statements = []
    counts = {"rejected": 0, "failed": 0, "read": 0}
    for reading in get_safety_readings(conn, nct_id):
        if reading["system"] == "llm":
            counts["read"] += 1
            if reading["status"] != "ok":
                counts["failed"] += 1
        for row in reading["statements"]:
            common = {"system": reading["system"], "pmid": reading["pmid"], "kind": row["kind"], "term": row["term"],
                      "sentence": row["sentence"], "negated": row["negated"]}
            if reading["system"] == "baseline":
                if row["numbers"] or row["negated"]:
                    statements.append({**common, "numbers": row["numbers"] or []})
                continue
            if row["rejected_code"] is not None:
                counts["rejected"] += 1
                continue
            comparison = compare_statement(statement_from_row(row), safety)
            statements.append({
                **common, "group": row["arm"], "count": row["affected"], "denominator": row["denominator"],
                "percent": row["percent"], "grade": row["grade"], "related": row["treatment_related"],
                "category": comparison.category,
            })
    return statements, counts


def score_trials(conn, trials: list[str]) -> dict:
    """All the rubric's counts over a list of trials."""
    result = {
        "trials": len(trials), "abstracts": 0, "labels": 0, "labels_by_category": {category: 0 for category in CATEGORIES},
        "empty_abstracts": 0, "rejected": 0, "failed": 0, "readings": 0,
        "llm": {"statements": 0, "matched": 0, "empty_right": 0, "said": {category: 0 for category in CATEGORIES}},
        "baseline": {"statements": 0, "matched": 0, "empty_right": 0},
        "fields": {field: 0 for field in FIELDS}, "all_fields_right": 0,
        "c1_right": 0, "c2_right": 0, "false_consistent": 0,
        "differences": 0, "differences_routed": 0, "per_trial": {}, "misses": [],
    }
    for nct_id in trials:
        labels, empty = parse_labels(FOLDER / "trials" / nct_id / "labels.txt")
        statements, counts = system_statements(conn, nct_id)
        findings = {finding.check: finding.category for finding in check_safety_summary(conn, nct_id)}
        result["abstracts"] += len(empty) + len({label["pmid"] for label in labels})
        result["labels"] += len(labels)
        result["empty_abstracts"] += len(empty)
        result["rejected"] += counts["rejected"]
        result["failed"] += counts["failed"]
        result["readings"] += counts["read"]
        trial_line = {"labels": len(labels)}

        for system in ("llm", "baseline"):
            own = [statement for statement in statements if statement["system"] == system]
            pairs = match_one_to_one(labels, own)
            result[system]["statements"] += len(own)
            result[system]["matched"] += len(pairs)
            for pmid in empty:
                if not any(statement["pmid"] == pmid for statement in own):
                    result[system]["empty_right"] += 1
            trial_line[system] = f"{len(pairs)} of {len(labels)} found, {len(own)} stated"
            if system == "baseline":
                continue

            # the model path: fields, comparison, false "consistent"
            matched_statements = set(pairs.values())
            for index, statement in enumerate(own):
                result["llm"]["said"][statement["category"]] = result["llm"]["said"].get(statement["category"], 0) + 1
                if statement["category"] == "safety_consistent":
                    label_category = None
                    for label_index, statement_index in pairs.items():
                        if statement_index == index:
                            label_category = labels[label_index]["category"]
                    if index not in matched_statements or label_category != "safety_consistent":
                        result["false_consistent"] += 1
            for label_index, label in enumerate(labels):
                result["labels_by_category"][label["category"]] += 1
                if label_index not in pairs:
                    result["misses"].append(f"{nct_id} PMID {label['pmid']} sentence {label['sentence']} {label['kind']} {label['term'] or ''}: not found by the model")
                    continue
                statement = own[pairs[label_index]]
                all_right = True
                for field in FIELDS:
                    if field_is_right(field, label, statement):
                        result["fields"][field] += 1
                    else:
                        all_right = False
                if all_right:
                    result["all_fields_right"] += 1
                    if statement["category"] == label["category"]:
                        result["c1_right"] += 1
                if statement["category"] == label["category"]:
                    result["c2_right"] += 1

        # routing: a labelled difference must sit in a high-priority finding of its quantity
        for label in labels:
            if label["category"] in DIFFERENCES:
                result["differences"] += 1
                check = "safety_summary:" + quantity_name(label["kind"], label["term"]).replace(" ", "_")
                if findings.get(check) in SAFETY_HIGH_RISK:
                    result["differences_routed"] += 1
                else:
                    result["misses"].append(f"{nct_id} PMID {label['pmid']} sentence {label['sentence']}: labelled difference not in a high-priority finding")
        result["per_trial"][nct_id] = trial_line
    return result


def keep_rule(result: dict) -> dict:
    """The four gates of rubric section 5. Each is 'met', 'not met' or 'not tested'."""
    labels = result["labels"]
    gates = {}
    if labels == 0:
        gates["1 recall at least 10 points above the baseline"] = "not tested"
    else:
        gap = 100 * (result["llm"]["matched"] - result["baseline"]["matched"]) / labels
        gates["1 recall at least 10 points above the baseline"] = "met" if gap >= 10 else "not met"
    stated = result["llm"]["statements"]
    if stated == 0:
        gates["2 precision at least 80%"] = "not tested"
    else:
        gates["2 precision at least 80%"] = "met" if 100 * result["llm"]["matched"] / stated >= 80 else "not met"
    gates["3 no false consistent"] = "met" if result["false_consistent"] == 0 else "not met"
    if result["llm"]["said"]["safety_consistent"] == 0:
        gates["3 no false consistent"] += " (but the system never answered consistent, so this shows little)"
    if result["differences"] == 0:
        gates["4 no labelled difference misses review"] = "not tested"
    else:
        gates["4 no labelled difference misses review"] = "met" if result["differences_routed"] == result["differences"] else "not met"
    return gates


def print_result(title: str, result: dict) -> None:
    labels = result["labels"]
    print(f"\n=== {title}: {result['trials']} trials, {result['abstracts']} abstracts, {labels} labelled statements ===")
    print("Labels by category:", ", ".join(f"{category.replace('safety_', '')} {count}" for category, count in result["labels_by_category"].items()))
    for system in ("llm", "baseline"):
        name = "model" if system == "llm" else "baseline"
        print(f"E1 recall    {name}: {rate_text(result[system]['matched'], labels)}")
        print(f"E2 precision {name}: {rate_text(result[system]['matched'], result[system]['statements'])}")
        print(f"E4 empty abstracts kept empty, {name}: {rate_text(result[system]['empty_right'], result['empty_abstracts'])}")
    matched = result["llm"]["matched"]
    for field in FIELDS:
        print(f"E3 {field} right (model, matched statements): {rate_text(result['fields'][field], matched)}")
    print(f"E5 model statements rejected by code: {result['rejected']}; readings failed: {result['failed']} of {result['readings']}")
    print(f"C1 category right, given every field right: {rate_text(result['c1_right'], result['all_fields_right'])}")
    print(f"C2 category right, end to end: {rate_text(result['c2_right'], labels)}")
    print(f"C3 false 'consistent': {result['false_consistent']} (the system answered consistent {result['llm']['said']['safety_consistent']} times, "
          f"number differs {result['llm']['said']['safety_number_differs']} times)")
    for category in ("safety_consistent", "safety_number_differs"):
        if result["labels_by_category"][category] == 0:
            print(f"   branch {category}: not tested (no labelled case)")
    print(f"R1 labelled differences reaching review: {rate_text(result['differences_routed'], result['differences'])}")
    print("Keep rule:")
    gates = keep_rule(result)
    for gate, answer in gates.items():
        print(f"   {gate}: {answer}")
    qualifies = all(answer.startswith("met") for answer in gates.values())
    print("   -> the model", "qualifies" if qualifies else "does NOT qualify", "as the primary safety reader (a gate that is 'not tested' is not met)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="a name for the saved result file")
    args = parser.parse_args()
    settings = Settings()
    first = (FOLDER / "trials.txt").read_text(encoding="utf-8").split()
    added = (FOLDER / "trials_extension.txt").read_text(encoding="utf-8").split()
    saved = {"name": args.name, "scored_at": datetime.now(timezone.utc).isoformat(),
             "rubric_sha256": sha256_of(FOLDER / "rubric.md"), "labels_sha256": {}}
    for nct_id in first + added:
        saved["labels_sha256"][nct_id] = sha256_of(FOLDER / "trials" / nct_id / "labels.txt")
    with psycopg.connect(settings.database_url) as conn:
        for title, trials in (("Whole pool", first + added), ("First 18 trials (original draw)", first), ("35 added trials", added)):
            result = score_trials(conn, trials)
            result["keep_rule"] = keep_rule(result)
            print_result(title, result)
            saved[title] = result
    print("\nNot found or not routed (whole pool):")
    for line in saved["Whole pool"]["misses"]:
        print("  ", line)
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"{args.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
