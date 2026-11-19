##############################################################################
# tests/test_report_assisted_review.py
#
# PURPOSE: Prove the assisted-review report counts the final outcome of each finding, the shares, the reasons and the
#          times correctly, and that it labels itself as assisted review (not accuracy).
##############################################################################

from evals.report_assisted_review import final_outcome, render, summarize


def row(decision, corrected=None, reason=None, seconds=60, evidence="s3"):
    return {"decision": decision, "corrected_status": corrected, "unresolved_reason": reason, "review_seconds": seconds, "evidence": evidence}


def test_final_outcome_from_the_last_review_row():
    assert final_outcome(row("accept_correction", "identified")) == "identified"
    assert final_outcome(row("accept_correction", "not_identified")) == "not_identified"
    assert final_outcome(row("accept", reason="generic_statement")) == "still_unclear"
    assert final_outcome(row("undetermined", reason="other")) == "unable"
    assert final_outcome(row("correct", "identified")) == "pending"


def test_counts_shares_reasons_and_times():
    rows = [row("accept_correction", "identified", seconds=30), row("accept_correction", "not_identified", seconds=90),
            row("accept", reason="generic_statement", seconds=60), row("accept", reason="generic_statement", seconds=700),
            row("undetermined", reason="abstract_too_short", seconds=120, evidence="")]
    result = summarize(rows, total=8)
    assert result["reviewed"] == 5 and result["not_yet_reviewed"] == 3 and result["settled"] == 2
    assert result["share_settled_of_reviewed"] == 0.4 and result["share_still_unclear_or_unable_of_reviewed"] == 0.6
    assert result["reasons_unclear"] == {"generic_statement": 2, "abstract_too_short": 1}
    assert result["seconds_median"] == 90 and result["seconds_total"] == 1000 and result["findings_over_limit"] == 1
    assert result["evidence_missing_where_required"] == 0                    # an "unable" row needs no evidence


def test_missing_evidence_is_counted_where_required():
    assert summarize([row("accept_correction", "identified", evidence="")], 1)["evidence_missing_where_required"] == 1


def test_an_empty_review_does_not_divide_by_zero_and_the_text_says_assisted_not_accuracy():
    result = summarize([], 56)
    assert result["share_settled_of_reviewed"] is None and result["reviewed"] == 0
    text = render(result)
    assert "ASSISTED review" in text and "not an independent accuracy measure" in text and "n/a" in text
