##############################################################################
# trialtrace/review/export_findings.py
#
# PURPOSE: Turn the review queue into a CSV or a Markdown report for people outside the app.
#          Each line keeps the model's finding AND the human review apart; "final_status" is filled only for
#          Human-verified findings (the accepted model status, or the explicitly accepted correction).
#
# USE:     rows = export_rows(load_queue(conn, nct_id))
#          csv_text = to_csv(rows)         markdown_text = to_markdown(rows, "NCT04644237")
##############################################################################

import csv
import io

from trialtrace.review.match_review import HUMAN_VERIFIED, STATES, count_states

STATE_LABELS = {"needs_review": "Needs review", "human_verified": "Human-verified", "unresolved": "Unresolved"}
STATE_BY_LABEL = {label: state for state, label in STATE_LABELS.items()}

COLUMNS = (
    "nct_id", "outcome_role", "outcome_number", "registered_outcome", "pmid",
    "model_status", "model_matched_name", "model_quote", "model_reason",
    "review_state", "final_status", "final_matched_name",
    "decision", "reviewer", "reviewed_at", "note", "evidence", "unresolved_reason", "review_seconds", "corrected_status", "corrected_matched_name",
    "ai_reviewer", "ai_decision", "ai_corrected_status", "ai_evidence", "ai_reason",
    "model", "served_model", "prompt_hash", "schema_version", "registry_hash", "paper_raw_hash", "match_run_id",
)


def final_values(row: dict) -> tuple[str, str]:
    """The settled answer, only for a Human-verified finding: the accepted correction, or the model's own answer."""
    if row["state"] != HUMAN_VERIFIED:
        return "", ""
    if row["decision"] == "accept_correction":
        return row["corrected_status"] or "", row["corrected_matched_name"] or ""
    return row["status"], row["matched_name"] or ""


def export_rows(queue: list[dict]) -> list[dict]:
    """One flat dict per queue row, with the columns above."""
    rows = []
    for item in queue:
        final_status, final_name = final_values(item)
        reviewed = item["review_id"] is not None
        rows.append({
            "nct_id": item["nct_id"], "outcome_role": item["outcome_role"], "outcome_number": item["outcome_position"] + 1,
            "registered_outcome": item["outcome_text"], "pmid": item["pmid"],
            "model_status": item["status"], "model_matched_name": item["matched_name"] or "",
            "model_quote": item["quote_text"] or "", "model_reason": item["reason"] or "",
            "review_state": STATE_LABELS[item["state"]], "final_status": final_status, "final_matched_name": final_name,
            "decision": item["decision"] if reviewed else "", "reviewer": item["reviewer"] if reviewed else "",
            "reviewed_at": f"{item['reviewed_at']:%Y-%m-%d %H:%M}" if reviewed else "", "note": (item["note"] or "") if reviewed else "",
            "evidence": (item.get("evidence") or "") if reviewed else "", "unresolved_reason": (item.get("unresolved_reason") or "") if reviewed else "",
            "review_seconds": (item.get("review_seconds") if item.get("review_seconds") is not None else "") if reviewed else "",
            "corrected_status": (item["corrected_status"] or "") if reviewed else "",
            "corrected_matched_name": (item["corrected_matched_name"] or "") if reviewed else "",
            "ai_reviewer": item.get("ai_reviewer") or "", "ai_decision": item.get("ai_decision") or "",
            "ai_corrected_status": item.get("ai_corrected_status") or "", "ai_evidence": item.get("ai_evidence") or "",
            "ai_reason": item.get("ai_reason") or "",
            "model": item["model"] or "", "served_model": item["served_model"] or "", "prompt_hash": item["prompt_hash"] or "",
            "schema_version": item["schema_version"] or "", "registry_hash": item["registry_hash"],
            "paper_raw_hash": item["paper_raw_hash"], "match_run_id": item["match_run_id"],
        })
    return rows


def safe_cell(value) -> str:
    """Text for a spreadsheet cell. A cell starting with = + - @ could run as a formula, so it gets a leading quote mark."""
    text = str(value)
    if text and text[0] in "=+-@\t\r":
        return "'" + text
    return text


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(row[column]) for column in COLUMNS])
    return buffer.getvalue()


def to_markdown(rows: list[dict], title: str) -> str:
    """A readable report: the counts, then each finding with its review."""
    counts = count_states([{"state": STATE_BY_LABEL[row["review_state"]]} for row in rows])
    lines = [f"# TrialTrace findings: {title}", "",
             "Model findings are AI-identified and provisional. Only findings marked Human-verified were accepted by a person. "
             "'Not identified' in an abstract does not mean the full paper omits the outcome. Model status says what the model concluded about "
             "an outcome; it is not clinical risk or importance.", "",
             "Counts: " + ", ".join(f"{STATE_LABELS[state]} {counts[state]}" for state in STATES), ""]
    for row in rows:
        lines.append(f"## {row['nct_id']} {row['outcome_role']} {row['outcome_number']}, PMID {row['pmid']}: {row['review_state']}")
        lines.append(f"- Registered outcome: {row['registered_outcome']}")
        lines.append(f"- Model finding: {row['model_status']}" + (f" ({row['model_matched_name']})" if row["model_matched_name"] else ""))
        if row["model_quote"]:
            lines.append(f"- Quote: \"{row['model_quote']}\"")
        if row["model_reason"]:
            lines.append(f"- Model reason: {row['model_reason']}")
        if row["decision"]:
            lines.append(f"- Review: {row['decision']} by {row['reviewer']} at {row['reviewed_at']}"
                         + (f"; corrected to {row['corrected_status']}" if row["corrected_status"] else "")
                         + (f"; note: {row['note']}" if row["note"] else ""))
        if row["final_status"]:
            lines.append(f"- Final (human-verified): {row['final_status']}" + (f" ({row['final_matched_name']})" if row["final_matched_name"] else ""))
        lines.append(f"- Versions: model {row['model']} (served as {row['served_model'] or 'unknown'}), prompt {row['prompt_hash'][:10]}, "
                     f"schema {row['schema_version']}, registry {row['registry_hash'][:10]}, paper file {row['paper_raw_hash'][:10]}, run {row['match_run_id']}")
        lines.append("")
    return "\n".join(lines)
