##############################################################################
# trialtrace/checks/results_posted.py
#
# PURPOSE: Question 1: does the registry record show posted results?
#          Three answers, never a bare yes/no:
#            results_available | results_not_in_snapshot | unknown
#          Says only what ONE downloaded record showed. It does not judge
#          whether results were due.
#
# USE:     from trialtrace.checks.results_posted import check_results_posted
#          finding = check_results_posted("NCT04644237", snapshot, evidence)
##############################################################################

from trialtrace.contracts import Evidence, Finding, Snapshot

RESULTS_PATH = "hasResults"             # where the value sits in the registry JSON


def check_results_posted(nct_id: str, snapshot: Snapshot | None, evidence: list[Evidence]) -> Finding:
    """Registry results availability -> one Finding."""

    def unknown(reason: str) -> Finding:
        return Finding(
            nct_id=nct_id, check="results_posted", category="unknown",
            claim="Whether the registry shows posted results is unknown.",
            limitations=[reason],
        )

    if snapshot is None:
        return unknown("No stored registry record: never ingested, or every fetch failed.")
    if snapshot.has_results is None:
        return unknown("The registry record has no usable hasResults value.")

    # The evidence must come from THIS snapshot's file, not an older version
    cited = [
        e.evidence_id for e in evidence
        if e.field_path == RESULTS_PATH and e.content_hash == snapshot.content_hash
    ]
    if not cited:
        return unknown("The hasResults value has no stored evidence.")   # refuse a claim we cannot cite

    when = f"{snapshot.retrieved_at:%Y-%m-%d}"
    if snapshot.has_results:
        return Finding(
            nct_id=nct_id, check="results_posted", category="results_available",
            claim=f"The registry record retrieved {when} shows posted results.",
            evidence_ids=cited,
            limitations=["Reflects the registry record at retrieval time."],
        )
    return Finding(
        nct_id=nct_id, check="results_posted", category="results_not_in_snapshot",
        claim=f"The registry record retrieved {when} does not show posted results.",
        evidence_ids=cited,
        limitations=[
            "Reflects the registry record at retrieval time; results may be posted later.",
            "Says nothing about whether results were due; no deadline is assessed.",
        ],
    )
