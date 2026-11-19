##############################################################################
# evals/run_safety_challenge.py
#
# PURPOSE: Run the safety CHALLENGE SET (evals/safety/challenge/cases.json): made-up abstracts beside
#          made-up registry tables, written to exercise numbers that agree and numbers that differ with
#          explicit denominators. NOT real papers. Its scores stand on their own: they are never added
#          to the pool evaluation (evals/run_safety.py) and never used in the keep rule.
#
# TWO MODES:
#   --rules-only   no model: the expected statements are given straight to the comparison rules.
#                  It shows what the RULES do when the reading is perfect.
#   (default)      the model reads each made-up abstract (one call each, cost), code checks its
#                  statements, then the rules compare them. It shows the whole path.
#
# USE:     python -m evals.run_safety_challenge --rules-only
#          python -m evals.run_safety_challenge --name challenge-final
##############################################################################

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.run_safety import fits, match_one_to_one, rate_text
from trialtrace.safety.safety_compare import compare_statement
from trialtrace.safety.safety_llm import ModelSafetyStatement, read_safety_with_llm
from trialtrace.config import Settings
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction
from trialtrace.paper_reader import reader_settings
from trialtrace.parse.pubmed_parser import Paper

CASES = Path(__file__).parent / "safety" / "challenge" / "cases.json"
RUNS_DIR = Path(__file__).parent / "runs"
NCT = "NCT99999999"             # not a real trial
HASH = "c" * 64


def build_registry(registry: dict) -> SafetyExtraction:
    """The made-up registry table of one case as the same object the real parser gives."""
    arms = []
    group_ids = {}
    for position, arm in enumerate(registry["arms"]):
        group_id = f"EG00{position}"
        group_ids[arm["title"]] = group_id
        arms.append(SafetyArm(
            nct_id=NCT, content_hash=HASH, position=position, group_id=group_id, title=arm["title"],
            deaths_affected=arm["deaths"][0], deaths_at_risk=arm["deaths"][1],
            serious_affected=arm["serious"][0], serious_at_risk=arm["serious"][1],
        ))
    events = []
    for position, event in enumerate(registry["events"]):
        counts = []
        for title, (affected, at_risk) in event["counts"].items():
            counts.append(SafetyEventCount(group_id=group_ids[title], affected=affected, at_risk=at_risk))
        events.append(SafetyEvent(nct_id=NCT, content_hash=HASH, kind=event["kind"], position=position, term=event["term"], counts=counts))
    return SafetyExtraction(status="ok", frequency_threshold=registry["threshold"], arms=arms, events=events)


def as_label(case_id: str, expected: dict) -> dict:
    """An expected statement in the shape the pool scorer's matching uses."""
    return {
        "pmid": case_id, "sentence": 0, "kind": expected["kind"], "term": expected.get("term"), "group": expected.get("group"),
        "count": expected.get("count"), "denominator": expected.get("denominator"), "percent": expected.get("percent"),
        "category": expected["category"],
    }


def rules_only(case: dict, registry: SafetyExtraction) -> list[dict]:
    """The expected statements, read perfectly, through the comparison rules."""
    statements = []
    for expected in case["expected"]:
        statement = ModelSafetyStatement(
            kind=expected["kind"], term=expected.get("term"), arm=expected.get("group"),
            arm_in_quote=True if expected.get("group") else None, count=expected.get("count"),
            denominator=expected.get("denominator"), percent=expected.get("percent"), grade=expected.get("grade"),
            treatment_related=expected.get("related", False), negated=expected.get("count") == 0,
        )
        statements.append(as_system(case["id"], statement, registry))
    return statements


def as_system(case_id: str, statement: ModelSafetyStatement, registry: SafetyExtraction) -> dict:
    comparison = compare_statement(statement, registry)
    return {
        "system": "llm", "pmid": case_id, "sentence": 0, "kind": statement.kind, "term": statement.term, "negated": statement.negated,
        "group": statement.arm, "count": statement.count, "denominator": statement.denominator, "percent": statement.percent,
        "category": comparison.category, "reason": comparison.reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules-only", action="store_true", help="no model call: expected statements go straight to the rules")
    parser.add_argument("--name", default="challenge", help="a name for the saved result file")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    reader = None if args.rules_only else reader_settings(Settings())

    labels_total, found, stated, right = 0, 0, 0, 0
    by_category: dict[str, list[int]] = {}             # labelled category -> [right, total]
    false_consistent, rejected = 0, 0
    saved = {"name": args.name, "mode": "rules-only" if args.rules_only else "model", "cases": []}
    for case_index, case in enumerate(cases, 1):
        registry = build_registry(case["registry"])
        raw = None
        if args.rules_only:
            statements = rules_only(case, registry)
        else:
            # Paper requires a numeric PMID; keep ch01 etc. as the scoring/report IDs.
            reading = read_safety_with_llm(Paper(pmid=str(900000000 + case_index), title="challenge", abstract=case["abstract"]), reader)
            raw = reading.llm.text if reading.llm else None
            rejected += len(reading.rejected)
            statements = [as_system(case["id"], statement, registry) for statement in reading.statements]
        labels = [as_label(case["id"], expected) for expected in case["expected"]]
        pairs = match_one_to_one(labels, statements)
        labels_total += len(labels)
        found += len(pairs)
        stated += len(statements)
        lines = []
        for index, label in enumerate(labels):
            tally = by_category.setdefault(label["category"], [0, 0])
            tally[1] += 1
            answer = statements[pairs[index]]["category"] if index in pairs else "not found"
            if answer == label["category"]:
                tally[0] += 1
                right += 1
            lines.append(f"{label['kind']} {label['term'] or ''} {label['group'] or 'all'}: expected {label['category']}, got {answer}")
        for index, statement in enumerate(statements):
            if statement["category"] == "safety_consistent":
                matched_label = [label_index for label_index, statement_index in pairs.items() if statement_index == index]
                if not matched_label or labels[matched_label[0]]["category"] != "safety_consistent":
                    false_consistent += 1
        print(f"{case['id']} ({case['why']})")
        for line in lines:
            print("   ", line)
        saved["cases"].append({"id": case["id"], "lines": lines, "statements": statements, "raw_answer": raw})

    print(f"\nCHALLENGE SET ({saved['mode']}; made-up cases, reported on its own)")
    print(f"Statements found: {rate_text(found, labels_total)}; system statements that match one: {rate_text(found, stated)}")
    print(f"Category right, end to end: {rate_text(right, labels_total)}")
    for category, (good, total) in sorted(by_category.items()):
        print(f"   labelled {category}: {rate_text(good, total)}")
    print(f"False 'consistent': {false_consistent}; statements rejected by code: {rejected}")
    saved["summary"] = {"labels": labels_total, "found": found, "stated": stated, "right": right,
                        "by_category": by_category, "false_consistent": false_consistent, "rejected": rejected}
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"safety-{args.name}-{saved['mode']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
