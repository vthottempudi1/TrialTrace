##############################################################################
# trialtrace/compare/paper_agreement.py
#
# PURPOSE: How the LLM's answer and the code baseline's answer about one outcome in one abstract
#          compare, and how urgently a person should look. Pure rules, no database, no model.
#
# USE:     agreement, risk, why = compare_predictions(baseline_status, llm_status, llm_error_code)
##############################################################################

# an LLM answer that code rejected because its evidence did not check out
EVIDENCE_ERRORS = (
    "quote_not_found", "name_not_in_quote", "missing_quote", "missing_name",
    "invalid_quote_type", "invalid_name_type", "empty_quote", "empty_name",
)


def compare_predictions(baseline_status: str | None, llm_status: str, llm_error: str | None) -> tuple[str, str, str]:
    """(agreement, risk, why). Risk is the review priority: disagreements, unclear answers and failures come first."""
    if baseline_status is None:
        if llm_status == "failed":
            agreement = "llm_evidence_invalid" if llm_error in EVIDENCE_ERRORS else "llm_failed"
            return agreement, "high", f"the model answer could not be used ({llm_error}); no baseline was run"
        if llm_status == "no_text":
            return "no_text", "high", "the paper has no abstract text; no answer was inferred"
        if llm_status == "ambiguous":
            return "llm_unclear", "high", "the model could not match the outcome exactly"
        if llm_status == "identified":
            return "llm_identified", "low", "the model identified supporting evidence; no baseline was run"
        if llm_status == "not_identified":
            return "llm_not_identified", "low", "the model did not identify a result in the abstract; no baseline was run"
        return "llm_failed", "high", "the model returned an unsupported status; review is required"
    if llm_status == "failed":
        if llm_error in EVIDENCE_ERRORS:
            return ("llm_evidence_invalid", "high",
                    f"the model's evidence failed a code check ({llm_error}); the baseline answer is only a provisional fallback")
        return ("llm_failed", "high",
                f"the model could not answer ({llm_error}); the baseline answer is only a provisional fallback")
    if llm_status == "no_text":
        return "no_text", "low", "the paper has no abstract text, so nothing could be checked"
    if llm_status == "ambiguous":
        return "llm_unclear", "high", "the model could not match the outcome exactly (scope, time frame or measure differs)"
    if llm_status == baseline_status:
        if llm_status == "identified":
            return "agree_identified", "low", "both systems found a result for this outcome"
        return "agree_not_identified", "low", "neither system found a result in the abstract"
    if llm_status == "identified":
        return "llm_only", "high", "the model found a result that the baseline did not"
    if baseline_status == "identified":
        return "baseline_only", "high", "the baseline found a result that the model did not"
    return "different", "medium", "the systems gave different answers about whether the outcome is reported"
