##############################################################################
# evals/challenge_tools.py
#
# PURPOSE: Small, tested helpers for challenge set v1 (rule: evals/challenge_v1/RULE.md):
#          - read the failure-mode tags that the labeller wrote at the start of a label note ("FM=subgroup,time; ...");
#          - tally model answers per confirmed failure mode, with the wrong "identified" answers listed;
#          - compare two label sets (original agreement) and list the disagreements.
#          A string match only makes a trial a candidate. A failure mode counts as CONFIRMED only where the labeller
#          wrote its tag on a pair, so these counts come from the labels, not from the string screen.
##############################################################################

MODES = ("attr", "subgroup", "time", "bare")
MODE_NAMES = {"attr": "wrong trial or pooled results", "subgroup": "subgroup or partial results",
              "time": "different time point", "bare": "bare no-difference or qualitative statement"}
STATUS_TO_ANSWER = {"identified": "yes", "ambiguous": "unclear", "not_identified": "no"}


def read_tags(note: str) -> list[str]:
    """The failure-mode tags at the start of a note: 'FM=attr,subgroup; text' gives ['attr', 'subgroup']. 'FM=none' gives []."""
    note = note.strip()
    if not note.startswith("FM="):
        return []
    tags = note[3:].split(";")[0].split(",")
    return [tag.strip() for tag in tags if tag.strip() in MODES]


def tally_by_mode(pairs: list[dict]) -> dict:
    """pairs: dicts with label (yes/no/unclear), status (identified/ambiguous/not_identified/...) and tags.
    For each confirmed failure mode: how many pairs carry it, how the labels split, how many the model called identified
    although the label is not yes (wrong identified), and how many labelled yes it called identified."""
    result = {}
    for mode in MODES:
        subset = [p for p in pairs if mode in p["tags"]]
        result[mode] = {
            "pairs": len(subset),
            "labelled": {answer: sum(1 for p in subset if p["label"] == answer) for answer in ("yes", "unclear", "no")},
            "model_identified": sum(1 for p in subset if p["status"] == "identified"),
            "wrong_identified": sum(1 for p in subset if p["status"] == "identified" and p["label"] != "yes"),
            "model_unclear_on_labelled_unclear": sum(1 for p in subset if p["label"] == "unclear" and p["status"] == "ambiguous"),
            "labelled_unclear": sum(1 for p in subset if p["label"] == "unclear"),
        }
    return result


def compare_labels(first: dict[str, str], second: dict[str, str]) -> dict:
    """Original agreement of two label sets over the pairs both labelled: counts, a confusion table and the disagreements."""
    shared = sorted(set(first) & set(second))
    disagreements = [(key, first[key], second[key]) for key in shared if first[key] != second[key]]
    confusion = {}
    for key in shared:
        pair = f"{first[key]} -> {second[key]}"
        confusion[pair] = confusion.get(pair, 0) + 1
    agree = len(shared) - len(disagreements)
    return {"pairs": len(shared), "agree": agree, "share": agree / len(shared) if shared else None,
            "confusion": dict(sorted(confusion.items())), "disagreements": disagreements}


def cohens_kappa(first: dict[str, str], second: dict[str, str]) -> float | None:
    """Chance-corrected agreement of two label sets over the pairs both labelled; None when it cannot be worked out."""
    shared = sorted(set(first) & set(second))
    if not shared:
        return None
    answers = sorted({first[key] for key in shared} | {second[key] for key in shared})
    observed = sum(1 for key in shared if first[key] == second[key]) / len(shared)
    expected = sum((sum(1 for key in shared if first[key] == a) / len(shared)) * (sum(1 for key in shared if second[key] == a) / len(shared)) for a in answers)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def apply_reconciliation(drafts: dict[str, str], decisions: dict, disagreements: list[tuple]) -> dict[str, str]:
    """Final labels: the draft label, replaced where a reconciliation decision exists. Every disagreement needs a decision with a
    valid final answer and a written reason; a decision for a pair that was not in dispute is refused."""
    disputed = {key for key, _first, _second in disagreements}
    missing = sorted(disputed - set(decisions))
    if missing:
        raise ValueError(f"{len(missing)} disagreement(s) have no decision yet, for example {missing[0]}")
    extra = sorted(set(decisions) - disputed)
    if extra:
        raise ValueError(f"A decision exists for a pair that was not in dispute: {extra[0]}")
    final = dict(drafts)
    for key, decision in decisions.items():
        if decision.get("final") not in ("yes", "no", "unclear"):
            raise ValueError(f"{key}: the final answer must be yes, no or unclear")
        if not str(decision.get("reason", "")).strip():
            raise ValueError(f"{key}: a decision needs a reason from the source")
        final[key] = decision["final"]
    return final
