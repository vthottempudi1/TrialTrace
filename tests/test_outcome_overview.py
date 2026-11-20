##############################################################################
# tests/test_outcome_overview.py
#
# PURPOSE: The outcome overview groups per-paper findings by registered outcome, uses the roll-up rule for the
#          outcome status, keeps review priority separate from status, and counts progress before any filtering.
##############################################################################

from trialtrace.review.match_review import outcome_overview, sort_overview


def row(role, position, status, pmid, state="needs_review", registry_hash="h1", text=None):
    return {"nct_id": "NCT1", "registry_hash": registry_hash, "outcome_role": role, "outcome_position": position,
            "outcome_text": text or f"{role} {position}", "status": status, "pmid": pmid, "state": state}


def test_one_line_per_outcome_in_registry_order():
    rows = [row("secondary", 0, "identified", "1"), row("primary", 0, "identified", "1"),
            row("primary", 0, "identified", "2"), row("secondary", 1, "ambiguous", "1")]
    lines = outcome_overview(rows)
    assert [(l["role"], l["position"], l["papers"]) for l in lines] == [("primary", 0, 2), ("secondary", 0, 1), ("secondary", 1, 1)]


def test_same_position_in_two_roles_stays_two_outcomes():
    lines = outcome_overview([row("primary", 0, "identified", "1"), row("secondary", 0, "identified", "1")])
    assert len(lines) == 2


def test_different_registry_versions_stay_separate():
    lines = outcome_overview([row("primary", 0, "identified", "1", registry_hash="a"), row("primary", 0, "identified", "1", registry_hash="b")])
    assert len(lines) == 2


def test_status_follows_the_rollup_rule():
    assert outcome_overview([row("primary", 0, "identified", "1"), row("primary", 0, "ambiguous", "2")])[0]["status"] == "identified"
    assert outcome_overview([row("primary", 0, "ambiguous", "1"), row("primary", 0, "not_identified", "2")])[0]["status"] == "unclear"
    assert outcome_overview([row("primary", 0, "not_identified", "1")])[0]["status"] == "not_identified"


def test_identified_outcome_with_an_ambiguous_paper_gets_high_priority():
    line = outcome_overview([row("primary", 0, "identified", "1"), row("primary", 0, "ambiguous", "2")])[0]
    assert line["status"] == "identified" and line["priority"] == 0


def test_priority_order_puts_flagged_first_and_keeps_registry_order_inside_a_level():
    lines = outcome_overview([row("primary", 0, "identified", "1"), row("secondary", 0, "ambiguous", "1"),
                              row("secondary", 1, "not_identified", "1"), row("secondary", 2, "ambiguous", "1")])
    ordered = sort_overview(lines, by_priority=True)
    assert [(l["role"], l["position"]) for l in ordered] == [("secondary", 0), ("secondary", 2), ("primary", 0), ("secondary", 1)]
    assert sort_overview(lines, by_priority=False) == lines


def test_review_progress_counts_reviewed_papers():
    line = outcome_overview([row("primary", 0, "identified", "1", state="human_verified"),
                             row("primary", 0, "identified", "2", state="needs_review")])[0]
    assert (line["reviewed"], line["papers"]) == (1, 2)
