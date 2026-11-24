##############################################################################
# trialtrace/evidence_expansion/eval/metrics.py
#
# PURPOSE: Score one configuration. Each row is one case:
#            {"role", "gold", "v1", "final", "quote_ok", "calls", "tokens"}
#          Metrics are reported separately for primary and secondary outcomes.
#
# USE:     table = score(rows)          # rows for ONE configuration
##############################################################################


def percent(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{part}/{whole}"


def score_group(rows: list[dict]) -> dict:
    """Metrics for one group of rows (for example all primary outcomes)."""
    wrong_at_start = [row for row in rows if row["v1"] != row["gold"]]
    resolved = [row for row in wrong_at_start if row["final"] == row["gold"]]

    false_upgrades = [row for row in rows if row["final"] == "identified" and row["gold"] != "identified"]

    should_abstain = [row for row in rows if row["gold"] == "not_identified"]
    abstained = [row for row in should_abstain if row["final"] != "identified"]

    identified = [row for row in rows if row["final"] == "identified" and row["v1"] != "identified"]
    valid_quotes = [row for row in identified if row["quote_ok"]]

    total_calls = sum(row["calls"] for row in rows)
    total_tokens = sum(row["tokens"] for row in rows)
    correct_final = [row for row in rows if row["final"] == row["gold"]]

    return {
        "cases": len(rows),
        "accuracy": percent(len(correct_final), len(rows)),
        "resolution": percent(len(resolved), len(wrong_at_start)),
        "false_upgrades": percent(len(false_upgrades), len(rows)),
        "correct_abstention": percent(len(abstained), len(should_abstain)),
        "quote_valid": percent(len(valid_quotes), len(identified)),
        "calls_total": total_calls,
        "tokens_total": total_tokens,
        "calls_per_resolved": round(total_calls / len(resolved), 1) if resolved else "n/a",
    }


def score(rows: list[dict]) -> dict:
    """Metrics for all, primary only and secondary only."""
    primary = [row for row in rows if row["role"] == "primary"]
    secondary = [row for row in rows if row["role"] != "primary"]
    return {"all": score_group(rows), "primary": score_group(primary), "secondary": score_group(secondary)}


def regression_on_controls(control_rows: list[dict]) -> str:
    """Controls were already correct (gold identified). Count how many would be changed wrongly."""
    wrong = [row for row in control_rows if row["final"] != row["gold"]]
    return percent(len(wrong), len(control_rows))
