"""Count unresolved registry wording pairs before deciding whether to add an LLM.

Read-only database audit. A candidate is not a confirmed semantic match.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from trialtrace.compare.registry_changes import check_registry_changes
from trialtrace.compare.reported_outcomes import check_reported_outcomes
from trialtrace.config import Settings
from trialtrace.db.repository import get_evidence_by_ids, list_trial_ids


def wording_pairs(nct_id, comparison, items):
    """Same-role unmatched names on both sides are candidates, not inferred equivalences."""
    before_kind, after_kind = (("removed", "added") if comparison == "registry_versions"
                               else ("registered_only", "reported_only"))
    pairs = []
    for old in items:
        if old.kind != before_kind:
            continue
        for new in items:
            if new.kind != after_kind or old.before != new.after:
                continue
            parts = [nct_id, comparison, old.measure_text, new.measure_text, old.before,
                     old.evidence_ids, new.evidence_ids]
            pair_id = hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:20]
            pairs.append({"id": pair_id, "nct_id": nct_id, "comparison": comparison,
                          "role": old.before, "before": old.measure_text, "after": new.measure_text,
                          "evidence_ids": list(dict.fromkeys(old.evidence_ids + new.evidence_ids)),
                          "classification": "unreviewed"})
    return pairs


def audit(conn, trial_ids):
    counts = {"registry_versions": Counter(), "registered_vs_posted": Counter()}
    pairs, exceptions = [], []
    for nct_id in trial_ids:
        for comparison, check in (("registry_versions", check_registry_changes),
                                  ("registered_vs_posted", check_reported_outcomes)):
            report = check(conn, nct_id)
            tally = counts[comparison]
            tally["trials_checked"] += 1
            tally["status:" + report.status] += 1
            if report.needs_review:
                tally["trials_with_unresolved_items"] += 1
            tally["unresolved_items"] += len(report.needs_review)
            for item in report.needs_review:
                tally["reason:" + item.kind] += 1
                if item.kind in ("ambiguous", "type_unreadable"):
                    exceptions.append({"nct_id": nct_id, "comparison": comparison, **item.model_dump()})
            candidates = wording_pairs(nct_id, comparison, report.needs_review)
            tally["candidate_pairs"] += len(candidates)
            if candidates:
                tally["trials_with_candidate_pairs"] += 1
            pairs.extend(candidates)
    ids = list({eid for pair in pairs for eid in pair["evidence_ids"]})
    evidence = get_evidence_by_ids(conn, ids)
    for pair in pairs:
        pair["evidence"] = [evidence[eid].model_dump(mode="json") for eid in pair["evidence_ids"] if eid in evidence]
        pair["missing_evidence_ids"] = [eid for eid in pair["evidence_ids"] if eid not in evidence]
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "Unresolved wording audit; no LLM calls and no semantic matches accepted.",
            "summary": {key: dict(value) for key, value in counts.items()},
            "candidate_pairs": pairs, "other_unresolved": exceptions,
            "decision": "Do not build a wording helper until candidate review establishes a useful resolvable set."}


def write_audit(result, directory):
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (directory / "review.csv").open("w", newline="", encoding="utf-8") as out:
        fields = ("id", "nct_id", "comparison", "role", "before", "after", "decision", "reason", "reviewer")
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for pair in result["candidate_pairs"]:
            writer.writerow({key: pair.get(key, "") for key in fields})
    lines = ["# Registry wording audit", "", result["purpose"], "",
             "Candidate pairs are same-role unmatched titles, not confirmed rewordings. Several candidates may",
             "compete for one outcome; pairs are not independent and must not be counted as resolved outcomes.", ""]
    for name, counts in result["summary"].items():
        lines += [f"## {name}", ""] + [f"- {key}: {value}" for key, value in counts.items()] + [""]
    lines += ["## Decision", "", result["decision"], "",
              "Review `review.csv` using `audit.json` and its exact source evidence. Use same_measure,",
              "different_scope, or unclear; explain measure, population and time-frame equivalence separately.",
              "A different role, time frame or definition is not a harmless wording change."]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/registry-wording-audit"))
    args = parser.parse_args()
    with psycopg.connect(Settings().database_url) as conn:
        result = audit(conn, list_trial_ids(conn))
    write_audit(result, args.out_dir)
    print(json.dumps(result["summary"], indent=2))
    print("Saved", args.out_dir / "report.md")


if __name__ == "__main__":
    main()
