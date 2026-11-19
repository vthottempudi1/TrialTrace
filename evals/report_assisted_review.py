##############################################################################
# evals/report_assisted_review.py
#
# PURPOSE: Report the assisted review of the model's unclear findings (instructions and frozen list in
#          evals/validation_v1/assisted_review/). For each frozen finding the FINAL review row counts. Reports
#          how many were reviewed, how many were settled (identified or not identified), how many stay unclear
#          and why, and the time on screen per finding.
#          This is ASSISTED review: the reviewer saw the model's answer. It is not an accuracy measure and the
#          report says so. No model call.
#
# USE:     python -m evals.report_assisted_review
##############################################################################

import json
import statistics
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trialtrace.config import Settings
from trialtrace.review.match_review import UNRESOLVED_REASONS

FOLDER = Path(__file__).parent / "validation_v1" / "assisted_review"
LONG_SECONDS = 600                      # a finding left on screen longer than this is flagged: the reviewer probably paused


def final_outcome(row: dict) -> str:
    """identified / not_identified / still_unclear / unable, from the final review row."""
    if row["decision"] == "accept_correction":
        return row["corrected_status"]
    if row["decision"] == "accept":
        return "still_unclear"
    if row["decision"] == "undetermined":
        return "unable"
    return "pending"                    # a correction that was never accepted


def summarize(rows: list[dict], total: int) -> dict:
    """Counts, shares, times and reasons from the final review rows (one per reviewed finding)."""
    outcomes = {"identified": 0, "not_identified": 0, "still_unclear": 0, "unable": 0, "pending": 0}
    reasons = {}
    seconds = []
    seconds_by_outcome = {}
    long_ones = 0
    missing_evidence = 0
    for row in rows:
        outcome = final_outcome(row)
        outcomes[outcome] += 1
        if outcome in ("still_unclear", "unable"):
            reason = row["unresolved_reason"] or "none recorded"
            reasons[reason] = reasons.get(reason, 0) + 1
        if outcome != "unable" and not (row["evidence"] or "").strip():
            missing_evidence += 1
        if row["review_seconds"] is not None:
            seconds.append(row["review_seconds"])
            seconds_by_outcome.setdefault(outcome, []).append(row["review_seconds"])
            if row["review_seconds"] > LONG_SECONDS:
                long_ones += 1
    reviewed = len(rows)
    settled = outcomes["identified"] + outcomes["not_identified"]
    return {
        "frozen_findings": total, "reviewed": reviewed, "not_yet_reviewed": total - reviewed,
        "outcomes": outcomes, "settled": settled,
        "share_settled_of_reviewed": settled / reviewed if reviewed else None,
        "share_still_unclear_or_unable_of_reviewed": (outcomes["still_unclear"] + outcomes["unable"]) / reviewed if reviewed else None,
        "reasons_unclear": dict(sorted(reasons.items(), key=lambda item: -item[1])),
        "seconds_median": statistics.median(seconds) if seconds else None,
        "seconds_mean": statistics.mean(seconds) if seconds else None,
        "seconds_total": sum(seconds), "findings_over_limit": long_ones, "limit_seconds": LONG_SECONDS,
        "seconds_median_by_outcome": {name: statistics.median(values) for name, values in seconds_by_outcome.items()},
        "evidence_missing_where_required": missing_evidence,
    }


def percent(value) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def render(result: dict, title: str = "Assisted review of the model's unclear findings") -> str:
    o = result["outcomes"]
    lines = [
        f"# {title}",
        "",
        "ASSISTED review: the reviewer saw the model's answer. This shows whether the dashboard helps a reviewer reach a justified "
        "decision. It is not an independent accuracy measure.",
        "",
        f"- Frozen findings: {result['frozen_findings']}; reviewed: {result['reviewed']}; not yet reviewed: {result['not_yet_reviewed']}.",
        f"- Settled (identified {o['identified']}, not identified {o['not_identified']}): {result['settled']} = {percent(result['share_settled_of_reviewed'])} of reviewed.",
        f"- Agreed it stays unclear: {o['still_unclear']}; unable to decide: {o['unable']} "
        f"(together {percent(result['share_still_unclear_or_unable_of_reviewed'])} of reviewed). Unfinished corrections: {o['pending']}.",
        ("- Time on screen per finding: not measured (no time was recorded for these findings)." if result["seconds_median"] is None else
         f"- Time on screen per finding: median {result['seconds_median']} s, mean {round(result['seconds_mean'])} s, total {result['seconds_total']} s; "
         f"{result['findings_over_limit']} finding(s) over {result['limit_seconds']} s (likely pauses; the median is the safer figure)."),
        f"- Median seconds by outcome: {result['seconds_median_by_outcome']}.",
        f"- Evidence missing where required: {result['evidence_missing_where_required']}.",
        "",
        "Reasons findings remain unclear:",
    ]
    for reason, count in result["reasons_unclear"].items():
        lines.append(f"- {UNRESOLVED_REASONS.get(reason, reason)}: {count}")
    if not result["reasons_unclear"]:
        lines.append("- none yet")
    lines += ["", "Limits: one reviewer; the model's answer was visible; the abstract only; the frozen list is the 56 pairs the model called unclear in the validation v1 run."]
    return "\n".join(lines) + "\n"


def main() -> None:
    selection = json.loads((FOLDER / "selection.json").read_text(encoding="utf-8"))
    ids = [item["match_id"] for item in selection["findings"]]
    with psycopg.connect(Settings().database_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # the FINAL review row of each finding, separately for AI reviewers (name ends with "(AI)") and for people
            cur.execute("SELECT DISTINCT ON (m.match_id, kind) m.match_id, (r.reviewer ~* '\\(ai\\)\\s*$') AS is_ai, r.decision, r.corrected_status, "
                        "r.evidence, r.unresolved_reason, r.review_seconds, r.reviewer, r.reviewed_at "
                        "FROM paper_matches m JOIN paper_match_reviews r ON r.input_hash = m.input_hash "
                        "CROSS JOIN LATERAL (SELECT (r.reviewer ~* '\\(ai\\)\\s*$') AS kind) k "
                        "WHERE m.match_id = ANY(%s) ORDER BY m.match_id, kind, r.id DESC", (ids,))
            rows = cur.fetchall()
    sections = {}
    for label, is_ai in (("AI reviewer(s)", True), ("Human reviewer(s)", False)):
        part = [row for row in rows if row["is_ai"] == is_ai]
        names = sorted({row["reviewer"] for row in part})
        sections[label] = (summarize(part, len(ids)), names)
    texts = []
    for label, (result, names) in sections.items():
        suffix = f" ({', '.join(names)})" if names else " (none yet)"
        texts.append(render(result, f"Assisted review of the model's unclear findings: {label}{suffix}"))
    text = "\n".join(texts)
    (FOLDER / "report.md").write_text(text, encoding="utf-8")
    (FOLDER / "report.json").write_text(json.dumps({label: result for label, (result, _names) in sections.items()}, indent=2), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
