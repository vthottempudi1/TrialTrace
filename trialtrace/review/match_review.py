##############################################################################
# trialtrace/review/match_review.py
#
# PURPOSE: Human review of the model's findings about registered outcomes and paper abstracts.
#          The finding in paper_matches is never changed; each review is a new row in paper_match_reviews.
#
# STATES of a finding:
#   needs_review    no review yet, or the model's answer changed since the review, or a correction awaits acceptance
#   human_verified  the newest review is accept or accept_correction
#   unresolved      the newest review is undetermined
##############################################################################

from psycopg.rows import dict_row

from trialtrace.compare.paper_rollup import rollup_statuses

NEEDS_REVIEW = "needs_review"
HUMAN_VERIFIED = "human_verified"
UNRESOLVED = "unresolved"
STATES = (NEEDS_REVIEW, HUMAN_VERIFIED, UNRESOLVED)

FINDING_STATUSES = ("identified", "not_identified", "ambiguous")

# why a finding stays unclear: a fixed list, so the reasons can be counted
UNRESOLVED_REASONS = {
    "generic_statement": "generic statement, does not name this measure or gives no number or test",
    "subgroup_or_partial": "only a subgroup, part of the participants or one part of a broad measure",
    "different_time_point": "a different time point than registered",
    "different_analysis_population": "a different analysis set or comparison than registered",
    "wrong_or_uncertain_trial": "the result may belong to another trial or is pooled",
    "different_measure": "a related but different measure",
    "predictor_only": "the measure is used only as a predictor or inside an association analysis",
    "abstract_too_short": "the abstract does not say enough to decide",
    "other": "other (explain in the note)",
}

# A reviewer whose name ends with "(AI)" is an AI reviewer. Its reviews are kept and shown, but they never make a finding
# Human-verified and never override a human review: the state comes from the newest HUMAN review only.


def is_ai_reviewer(name: str | None) -> bool:
    """True for a reviewer name that ends with (AI), for example "AI reviewer (AI)"."""
    return bool(name) and name.strip().lower().endswith("(ai)")


# the newest LLM answer of each trial, with the newest HUMAN review and the newest AI review of the same question (joined by input_hash)
QUEUE_SQL = r"""
SELECT m.match_id, m.nct_id, m.match_run_id, m.outcome_role, m.outcome_position, m.outcome_text, m.pmid,
       m.status, m.matched_name, m.quote_text, m.quote_start, m.quote_end, m.reason, m.model, m.served_model, m.prompt_hash,
       m.schema_version, m.registry_hash, m.paper_raw_hash, m.input_hash,
       r.id AS review_id, r.decision, r.reviewer, r.note, r.reviewed_at, r.evidence, r.unresolved_reason, r.review_seconds,
       r.corrected_status, r.corrected_matched_name,
       r.reviewed_status, r.reviewed_matched_name, r.reviewed_quote,
       a.reviewer AS ai_reviewer, a.decision AS ai_decision, a.corrected_status AS ai_corrected_status,
       a.evidence AS ai_evidence, a.unresolved_reason AS ai_reason, a.note AS ai_note, a.reviewed_at AS ai_reviewed_at
FROM paper_matches m
LEFT JOIN LATERAL (SELECT * FROM paper_match_reviews x WHERE x.input_hash = m.input_hash AND x.reviewer !~* '\(ai\)\s*$'
                   ORDER BY x.id DESC LIMIT 1) r ON TRUE
LEFT JOIN LATERAL (SELECT * FROM paper_match_reviews y WHERE y.input_hash = m.input_hash AND y.reviewer ~* '\(ai\)\s*$'
                   ORDER BY y.id DESC LIMIT 1) a ON TRUE
WHERE m.system = 'llm' AND m.status <> 'failed' AND m.input_hash IS NOT NULL
  AND m.match_run_id = (SELECT max(match_run_id) FROM paper_matches z WHERE z.nct_id = m.nct_id AND z.system = 'llm')
  {where}
ORDER BY m.nct_id, m.outcome_role, m.outcome_position, m.pmid
"""


def review_state(row: dict) -> str:
    """The state of one queue row. A review only counts if the finding is still the one the reviewer saw."""
    if row["review_id"] is None:
        return NEEDS_REVIEW
    same_finding = (row["reviewed_status"] == row["status"]
                    and row["reviewed_matched_name"] == row["matched_name"]
                    and row["reviewed_quote"] == row["quote_text"])
    if not same_finding:
        return NEEDS_REVIEW
    if row["decision"] in ("accept", "accept_correction"):
        return HUMAN_VERIFIED
    if row["decision"] == "undetermined":
        return UNRESOLVED
    return NEEDS_REVIEW                                          # "correct": proposed, not yet accepted


def load_queue(conn, nct_id: str | None = None) -> list[dict]:
    """Findings of the latest run (one trial, or all trials) with their review state."""
    where = "AND m.nct_id = %s" if nct_id else ""
    params = (nct_id,) if nct_id else ()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(QUEUE_SQL.format(where=where), params)
        rows = cur.fetchall()
    for row in rows:
        row["state"] = review_state(row)
    return rows


def load_by_match_ids(conn, match_ids: list[int]) -> list[dict]:
    """The findings with exactly these match ids (a frozen selection), with their review state. Keeps the order given."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(QUEUE_SQL.replace("AND m.match_run_id = (SELECT max(match_run_id) FROM paper_matches z WHERE z.nct_id = m.nct_id AND z.system = 'llm')",
                                      "AND m.match_id = ANY(%s)").format(where=""), (list(match_ids),))
        rows = {row["match_id"]: row for row in cur.fetchall()}
    ordered = [rows[match_id] for match_id in match_ids if match_id in rows]
    for row in ordered:
        row["state"] = review_state(row)
    return ordered


def count_states(rows: list[dict]) -> dict:
    counts = {state: 0 for state in STATES}
    for row in rows:
        counts[row["state"]] += 1
    return counts


def get_finding(conn, match_id: int) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM paper_matches WHERE match_id = %s AND system = 'llm'", (match_id,))
        finding = cur.fetchone()
    if finding is None:
        raise ValueError(f"No model finding with match_id {match_id}")
    if finding["input_hash"] is None:
        raise ValueError("This old finding has no input fingerprint and cannot be reviewed")
    return finding


def save_review(conn, match_id: int, reviewer: str, decision: str, note: str = "",
                corrected_status: str | None = None, corrected_matched_name: str | None = None,
                evidence: str = "", unresolved_reason: str | None = None, review_seconds: int | None = None) -> int:
    """Add one review row for a finding. The caller commits. Returns the new review id."""
    reviewer = reviewer.strip()
    note = note.strip()
    if not reviewer:
        raise ValueError("A reviewer name is required")
    finding = get_finding(conn, match_id)

    if decision == "correct":
        if corrected_status not in FINDING_STATUSES:
            raise ValueError("A correction needs a status: identified, not_identified or ambiguous")
        if not note:
            raise ValueError("A correction needs a note saying why")
    elif decision == "undetermined":
        if not note:
            raise ValueError("'Unable to determine' needs a note saying why")
        if unresolved_reason not in UNRESOLVED_REASONS:
            raise ValueError("'Unable to determine' needs a reason from the list")
    elif decision == "accept_correction":
        pending = latest_review(conn, finding["input_hash"], is_ai_reviewer(reviewer))
        same = pending is not None and pending["reviewed_status"] == finding["status"] \
            and pending["reviewed_matched_name"] == finding["matched_name"] and pending["reviewed_quote"] == finding["quote_text"]
        if not same or pending["decision"] != "correct":
            raise ValueError("There is no pending correction to accept for this finding")
        corrected_status = pending["corrected_status"]                  # accept exactly what was proposed
        corrected_matched_name = pending["corrected_matched_name"]
    elif decision != "accept":
        raise ValueError(f"Unknown decision: {decision}")

    if unresolved_reason is not None and unresolved_reason not in UNRESOLVED_REASONS:
        raise ValueError(f"Unknown reason: {unresolved_reason}")
    if decision in ("accept", "undetermined"):
        corrected_status = None
        corrected_matched_name = None
    cur = conn.execute(
        "INSERT INTO paper_match_reviews (match_id, input_hash, decision, reviewer, note, corrected_status, corrected_matched_name, "
        "reviewed_status, reviewed_matched_name, reviewed_quote, model, served_model, prompt_hash, schema_version, "
        "registry_hash, paper_raw_hash, evidence, unresolved_reason, review_seconds) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (match_id, finding["input_hash"], decision, reviewer, note or None, corrected_status, corrected_matched_name,
         finding["status"], finding["matched_name"], finding["quote_text"], finding["model"], finding["served_model"],
         finding["prompt_hash"], finding["schema_version"], finding["registry_hash"], finding["paper_raw_hash"],
         evidence.strip() or None, unresolved_reason, review_seconds),
    )
    return cur.fetchone()[0]


ASSISTED_OUTCOMES = ("identified", "not_identified", "still_unclear", "unable")


def save_assisted_review(conn, match_id: int, reviewer: str, outcome: str, evidence: str = "", reason: str | None = None,
                         note: str = "", seconds: int | None = None) -> int:
    """One decision of an assisted review of a model "unclear" finding. The reviewer SEES the model's answer.

    identified / not_identified : the reviewer settles it; evidence required. Saved as a correction followed by its
                                  explicit acceptance (the reviewer confirmed it on screen), so it counts as Human-verified.
    still_unclear               : the reviewer agrees it is unclear; evidence and a reason from the list required.
    unable                      : the reviewer cannot decide; a reason and a note required (stays Unresolved).
    Returns the id of the last review row. The caller commits.
    """
    if outcome not in ASSISTED_OUTCOMES:
        raise ValueError(f"Unknown outcome: {outcome}")
    finding = get_finding(conn, match_id)
    if finding["status"] != "ambiguous":
        raise ValueError("Assisted review is for findings the model called unclear")
    if outcome in ("identified", "not_identified"):
        if not evidence.strip():
            raise ValueError("Say which sentence or words in the abstract you relied on")
        save_review(conn, match_id, reviewer, "correct", note or "settled in assisted review", outcome,
                    finding["matched_name"], evidence, None, seconds)
        return save_review(conn, match_id, reviewer, "accept_correction", note, None, None, evidence, None, seconds)
    if outcome == "still_unclear":
        if not evidence.strip():
            raise ValueError("Say what the abstract says that keeps this unclear")
        if reason not in UNRESOLVED_REASONS:
            raise ValueError("Choose why it stays unclear")
        return save_review(conn, match_id, reviewer, "accept", note, None, None, evidence, reason, seconds)
    return save_review(conn, match_id, reviewer, "undetermined", note, None, None, evidence, reason, seconds)


def latest_review(conn, input_hash: str, ai: bool = False) -> dict | None:
    """The newest review of the question by an AI reviewer (ai=True) or by a human (ai=False). The two never mix."""
    operator = "~*" if ai else "!~*"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM paper_match_reviews WHERE input_hash = %s AND reviewer {operator} '\\(ai\\)\\s*$' ORDER BY id DESC LIMIT 1", (input_hash,))
        return cur.fetchone()


def counts_by_trial(rows: list[dict]) -> list[dict]:
    """One line per trial: its three state counts and total, trials with the most work waiting first."""
    per_trial = {}
    for row in rows:
        counts = per_trial.setdefault(row["nct_id"], {state: 0 for state in STATES})
        counts[row["state"]] += 1
    lines = []
    for nct_id, counts in per_trial.items():
        lines.append({"nct_id": nct_id, **counts, "total": sum(counts.values())})
    lines.sort(key=lambda line: (-line[NEEDS_REVIEW], line["nct_id"]))
    return lines


def filter_rows(rows: list[dict], statuses: list[str] | None = None, roles: list[str] | None = None, nct_text: str = "") -> list[dict]:
    """Keep rows by model status, outcome role and part of the NCT ID. An empty choice keeps everything."""
    nct_text = nct_text.strip().upper()
    kept = []
    for row in rows:
        if statuses and row["status"] not in statuses:
            continue
        if roles and row["outcome_role"] not in roles:
            continue
        if nct_text and nct_text not in row["nct_id"]:
            continue
        kept.append(row)
    return kept


# review risk of one finding: 0 = look first, 2 = look last
RISK_BY_STATUS = {"ambiguous": 0, "identified": 1, "not_identified": 2}


def risk_rank(row: dict) -> int:
    """0 = high, 1 = normal, 2 = low. Unknown statuses count as high, so nothing hides at the bottom."""
    return RISK_BY_STATUS.get(row["status"], 0)


def sort_by_risk(rows: list[dict]) -> list[dict]:
    """Highest risk first. The sort is stable, so inside one level the trial order stays as it was."""
    return sorted(rows, key=risk_rank)


# ---- Outcome overview: one row per registered outcome, built from the per-paper findings (nothing is approved at outcome level) ----

ROLE_ORDER = {"primary": 0, "secondary": 1, "other": 2}
OUTCOME_STATUS_NAMES = {"identified": "Identified", "unclear": "Unclear", "unknown": "Unknown", "not_identified": "Not identified"}


def outcome_key(row: dict) -> tuple:
    """What identifies one registered outcome: trial, registry version, role and position (positions restart in each role)."""
    return (row["nct_id"], row["registry_hash"], row["outcome_role"], row["outcome_position"])


def outcome_overview(rows: list[dict]) -> list[dict]:
    """One line per registered outcome, in registry order (primary first). Give it ALL findings of the trial, not a filtered list.

    The outcome status uses the same roll-up rule as the findings report. The review priority is separate: an outcome
    that is identified in one paper can still have an ambiguous paper, and then it is looked at earlier.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault(outcome_key(row), []).append(row)
    lines = []
    for key, group in groups.items():
        statuses = [row["status"] for row in group]
        status = rollup_statuses(statuses, False)
        counts = {name: statuses.count(name) for name in ("identified", "ambiguous", "not_identified")}
        reviewed = sum(1 for row in group if row["state"] != NEEDS_REVIEW)
        priority = 0 if counts["ambiguous"] or status in ("unclear", "unknown") else (1 if status == "identified" else 2)
        first = group[0]
        lines.append({"key": key, "role": first["outcome_role"], "position": first["outcome_position"], "text": first["outcome_text"],
                      "status": status, "papers": len(group), "counts": counts, "reviewed": reviewed, "priority": priority})
    lines.sort(key=lambda line: (ROLE_ORDER.get(line["role"], 3), line["position"]))
    return lines


def sort_overview(lines: list[dict], by_priority: bool) -> list[dict]:
    """Registry order (as given), or review priority first with registry order inside each level."""
    return sorted(lines, key=lambda line: line["priority"]) if by_priority else lines
