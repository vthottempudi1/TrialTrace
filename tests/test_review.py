##############################################################################
# tests/test_review.py
#
# PURPOSE: Prove review decisions follow the rules (reviewer required, reject
#          needs a reason, edit needs wording without verdict words), are kept
#          as history, and that the queue and the question-and-answer loop work.
#          Uses the test database; every test is rolled back.
##############################################################################

from datetime import date

import pytest

from trialtrace.contracts import Finding
from trialtrace.db.repository import get_review_history, get_stored_findings, save_run
from trialtrace.review.review import needs_review, record_review, review_run

RUN = "run-test"
CHANGE = Finding(nct_id="NCT90000001", check="registry_changes", category="registry_outcome_change",
                 claim="1 registered outcome difference(s).", evidence_ids=["e1"], limitations=["For review."])
RESULTS = Finding(nct_id="NCT90000001", check="results_posted", category="results_not_in_snapshot",
                  claim="No posted results shown.", evidence_ids=["e2"])
UNKNOWN = Finding(nct_id="NCT90000001", check="timeline", category="unknown", claim="Unknown.")
NO_CHANGE = Finding(nct_id="NCT90000002", check="registry_changes", category="no_registry_outcome_change",
                    claim="Same outcomes.", evidence_ids=["e3"])


@pytest.fixture
def run(conn):
    save_run(conn, RUN, date(2026, 11, 2), [CHANGE, RESULTS, UNKNOWN, NO_CHANGE])
    return RUN


def finding_id(finding):
    return f"{RUN}:{finding.nct_id}:{finding.check}"


def current(conn, finding):
    stored = {s.finding_id: s for s in get_stored_findings(conn, RUN)}
    return stored[finding_id(finding)]


class Script:
    """Feeds scripted answers to the loop and records everything it shows."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.shown = []

    def ask(self, question):
        return self.answers.pop(0)

    def show(self, text):
        self.shown.append(text)


# --- the rules ---------------------------------------------------------------

def test_an_approval_is_saved_with_the_reviewer(conn, run):
    record_review(conn, finding_id(CHANGE), "approved", "Dr Rao", reason="Checked on the website")
    stored = current(conn, CHANGE)
    assert (stored.review_decision, stored.reviewer, stored.review_reason) == ("approved", "Dr Rao", "Checked on the website")


@pytest.mark.parametrize("kwargs", [
    {"decision": "rejected", "reviewer": "Dr Rao"},                                   # no reason
    {"decision": "rejected", "reviewer": "Dr Rao", "reason": "   "},                  # blank reason
    {"decision": "edited", "reviewer": "Dr Rao"},                                      # no new wording
    {"decision": "approved", "reviewer": ""},                                          # no reviewer
    {"decision": "approved", "reviewer": "   "},
    {"decision": "maybe", "reviewer": "Dr Rao"},                                       # not a decision
    {"decision": "edited", "reviewer": "Dr Rao", "edited_claim": "This is a violation."},
    {"decision": "edited", "reviewer": "Dr Rao", "edited_claim": "Outcome switching occurred."},
    {"decision": "edited", "reviewer": "Dr Rao", "edited_claim": "The update was late."},
])
def test_a_decision_that_breaks_a_rule_is_refused_and_nothing_is_saved(conn, run, kwargs):
    with pytest.raises(ValueError):
        record_review(conn, finding_id(CHANGE), **kwargs)
    assert get_review_history(conn, finding_id(CHANGE)) == []


def test_ordinary_words_that_contain_a_verdict_word_are_allowed(conn, run):
    # "related" and "template" contain "late" but are not the word "late"
    record_review(conn, finding_id(CHANGE), "edited", "Dr Rao", edited_claim="The related template differs in time frame.")
    assert current(conn, CHANGE).edited_claim.startswith("The related template")


# --- history -----------------------------------------------------------------

def test_changing_your_mind_adds_a_review_and_keeps_the_old_one(conn, run):
    record_review(conn, finding_id(CHANGE), "approved", "Dr Rao")
    record_review(conn, finding_id(CHANGE), "rejected", "Dr Rao", reason="Registry typo, fixed later")
    assert current(conn, CHANGE).review_decision == "rejected"                    # the newest decision
    assert [h["decision"] for h in get_review_history(conn, finding_id(CHANGE))] == ["approved", "rejected"]


def test_the_finding_itself_is_never_changed_by_a_review(conn, run):
    record_review(conn, finding_id(CHANGE), "edited", "Dr Rao", edited_claim="One time frame differs.")
    stored = current(conn, CHANGE)
    assert stored.finding.claim == CHANGE.claim and stored.edited_claim == "One time frame differs."


# --- the queue ---------------------------------------------------------------

def queue_checks(conn, include_plain_facts=False):
    stored = get_stored_findings(conn, RUN)
    return sorted(s.finding.check for s in stored if needs_review(s, include_plain_facts))


def test_the_default_queue_holds_differences_not_plain_facts(conn, run):
    assert queue_checks(conn) == ["registry_changes"]                      # not the plain result, unknown or no-change


def test_asking_for_everything_adds_the_plain_facts_but_never_unknown_or_no_change(conn, run):
    assert queue_checks(conn, include_plain_facts=True) == ["registry_changes", "results_posted"]


def test_a_decided_finding_leaves_the_queue(conn, run):
    record_review(conn, finding_id(CHANGE), "approved", "Dr Rao")
    assert queue_checks(conn) == []
    assert queue_checks(conn, include_plain_facts=True) == ["results_posted"]


def test_a_finding_that_needs_review_is_in_the_default_queue(conn):
    needs = Finding(nct_id="NCT90000003", check="registry_changes", category="registry_outcome_needs_review",
                    claim="Could not be matched safely.", evidence_ids=["e4"])
    save_run(conn, RUN, date(2026, 11, 2), [needs])
    assert queue_checks(conn) == ["registry_changes"]


# --- the question-and-answer loop ---------------------------------------------

def test_the_loop_records_each_kind_of_decision(conn, run):
    # Findings come in order: registry_changes (NCT90000001), then results_posted
    script = Script("a", "Looks right", "e", "Only the time frame differs.", "")
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show, include_plain_facts=True)
    assert summary == {"approved": 1, "edited": 1, "rejected": 0, "unclear": 0, "skipped": 0}
    assert current(conn, CHANGE).review_decision == "approved"
    assert current(conn, RESULTS).edited_claim == "Only the time frame differs."


def test_a_rejection_keeps_asking_until_a_reason_is_given(conn, run):
    script = Script("r", "", "   ", "Registry typo", "s")
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show, include_plain_facts=True)
    assert summary["rejected"] == 1 and summary["skipped"] == 1
    assert current(conn, CHANGE).review_reason == "Registry typo"


def test_skip_leaves_a_finding_undecided_and_quit_stops_the_loop(conn, run):
    script = Script("s", "q")
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show, include_plain_facts=True)
    assert summary == {"approved": 0, "edited": 0, "rejected": 0, "unclear": 0, "skipped": 1}
    assert current(conn, CHANGE).review_decision is None and current(conn, RESULTS).review_decision is None


def test_an_unknown_answer_is_asked_again(conn, run):
    script = Script("x", "yes", "q")
    review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    assert script.answers == []                                          # both bad answers were consumed, then q


def test_a_verdict_word_in_an_edit_is_refused_in_the_loop_and_nothing_is_saved(conn, run):
    script = Script("e", "That is a violation.", "", "q")
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    assert summary["edited"] == 0 and summary["skipped"] == 1
    assert any("Not saved" in line for line in script.shown)
    assert current(conn, CHANGE).review_decision is None


def test_the_commit_function_runs_after_each_saved_decision(conn, run):
    commits = []
    script = Script("a", "", "a", "")
    review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show, commit=lambda: commits.append(1),
               include_plain_facts=True)
    assert len(commits) == 2


def test_a_finding_is_shown_with_its_evidence_and_limitations(conn, run):
    script = Script("q")
    review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    text = "\n".join(script.shown)
    assert "Evidence: e1" in text and "Limitation: For review." in text and "1 registered outcome difference(s)." in text


def test_nothing_to_review_finishes_at_once(conn):
    save_run(conn, RUN, date(2026, 11, 2), [UNKNOWN, NO_CHANGE])
    script = Script()
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    assert summary == {"approved": 0, "edited": 0, "rejected": 0, "unclear": 0, "skipped": 0}
    assert "0 finding(s) need a decision" in script.shown[0]
