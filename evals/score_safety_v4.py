"""Source support, target attribution and report quality are separate measurements."""
from evals.safety_v4_reference import FIELDS, field_correct, pair_statements
from trialtrace.safety.safety_llm import ModelSafetyStatement

REVIEW = {"safety_unclear", "safety_not_comparable", "safety_in_paper_not_registry", "unknown"}
DIFFERENCES = {"safety_number_differs", "safety_in_paper_not_registry"}


def score(items, outputs):
    counts = dict.fromkeys(("cases", "target_labels", "accepted_target_predictions", "target_matches",
        "source_labels", "raw_source_predictions", "supported_source_predictions", "source_inventory_matches",
        "all_fields_correct", "exact_report_category", "false_consistent", "consistent_predictions",
        "verified_comparisons", "differences", "differences_discovered_and_routed", "difference_statements_in_flagged_cases",
        "empty_target_cases", "empty_target_confident_empty", "empty_target_cases_with_predictions",
        "reading_failures", "rejected_statements", "helper_proposals", "helper_correct", "helper_window_claims",
        "helper_window_supported", "helper_failures", "helper_abstentions"), 0)
    fields = {f: {"correct": 0, "total": 0} for f in FIELDS}
    families = set()
    for item in items:
        case, reference = item["case"], item["reference"]
        out = outputs[case["case_id"]]
        source = [ModelSafetyStatement(**st) for st in out["source_reading"]["statements"]]
        accepted = [ModelSafetyStatement(**st) for st in out["reading"]["statements"]]
        all_gold = reference.statements
        target = [st for st in all_gold if st.attribution == "target"]
        source_pairs = pair_statements(all_gold, source)
        pairs = pair_statements(target, accepted)
        reverse = {si: gi for gi, si in pairs.items()}
        counts["cases"] += 1
        counts["target_labels"] += len(target)
        counts["accepted_target_predictions"] += len(accepted)
        counts["target_matches"] += len(pairs)
        counts["source_labels"] += len(all_gold)
        counts["raw_source_predictions"] += len(source)
        # Quoted main numbers alone do not support a claim assigned to the wrong
        # arm/population/cause. Detail-role metadata is scored separately.
        counts["supported_source_predictions"] += sum(
            all(field_correct(all_gold[gi], source[si], field) for field in FIELDS
                if field != "detail_level" and (field != "event_scope" or all_gold[gi].kind == "event"))
            for gi, si in source_pairs.items())
        counts["source_inventory_matches"] += len(source_pairs)
        counts["reading_failures"] += out["reading"]["status"] != "ok"
        counts["rejected_statements"] += len(out["reading"]["rejected"])
        counts["empty_target_cases"] += not target
        if not target:
            counts["empty_target_confident_empty"] += not accepted and out["reading"]["status"] == "ok"
            counts["empty_target_cases_with_predictions"] += bool(accepted)
        for gi, gold in enumerate(target):
            families.add((case["case_id"], gold.claim_family))
            difference = gold.category in DIFFERENCES
            counts["differences"] += difference
            si = pairs.get(gi)
            if si is None:
                counts["difference_statements_in_flagged_cases"] += difference and out["reading"]["status"] != "ok"
                continue
            comparison = out["comparisons"][si]
            counts["exact_report_category"] += comparison["category"] == gold.category
            routed = comparison["category"] in REVIEW or comparison["category"] == "safety_number_differs"
            counts["differences_discovered_and_routed"] += difference and routed
            counts["difference_statements_in_flagged_cases"] += difference and routed
            applicable = [f for f in FIELDS if f != "event_scope" or gold.kind == "event"]
            right = [field_correct(gold, accepted[si], f) for f in applicable]
            counts["all_fields_correct"] += all(right)
            for f, correct in zip(applicable, right):
                fields[f]["correct"] += correct
                fields[f]["total"] += 1
        for si, comparison in enumerate(out["comparisons"]):
            category = comparison["category"]
            counts["verified_comparisons"] += category in ("safety_consistent", "safety_number_differs")
            counts["consistent_predictions"] += category == "safety_consistent"
            counts["false_consistent"] += category == "safety_consistent" and (
                si not in reverse or target[reverse[si]].category != "safety_consistent")
            packet = out["helpers"].get(str(si))
            if not packet:
                continue
            counts["helper_failures"] += packet["status"] == "failed"
            counts["helper_abstentions"] += packet["status"] == "abstained"
            if packet["status"] not in ("ready", "abstained"):
                continue
            gold = target[reverse[si]] if si in reverse else None
            for name in ("arm", "event"):
                candidate = packet["proposal"][name]["candidate_id"]
                if candidate is not None:
                    counts["helper_proposals"] += 1
                    counts["helper_correct"] += bool(gold and candidate in getattr(gold, f"acceptable_{name}_ids"))
            window = packet["proposal"]["window"]["assessment"]
            if window != "unknown":
                counts["helper_window_claims"] += 1
                counts["helper_window_supported"] += bool(gold and window == gold.window)
    counts["claim_families"] = len(families)
    counts["fields"] = fields

    def accuracy(numerator, denominator, minimum=1, threshold=.95):
        n, d = counts[numerator], counts[denominator]
        if d < minimum:
            return "insufficient coverage" if d else "not tested"
        return "met" if n / d >= threshold else "not met"

    counts["targets"] = {
        "target_recall_95": accuracy("target_matches", "target_labels"),
        "target_precision_95": accuracy("target_matches", "accepted_target_predictions"),
        "all_field_agreement_95": accuracy("all_fields_correct", "target_matches"),
        "window_support_95": accuracy("helper_window_supported", "helper_window_claims", 10),
        "zero_wrong_helper_proposals": "not met" if counts["helper_correct"] < counts["helper_proposals"] else accuracy("helper_correct", "helper_proposals", 30, 1),
        "zero_false_consistent": "not met" if counts["false_consistent"] else ("met" if counts["consistent_predictions"] >= 10 else "not tested"),
        "differences_discovered_and_routed": accuracy("differences_discovered_and_routed", "differences", 10, 1),
        "human_resolution_accuracy_and_time": "not tested by automated scoring"}
    return counts
