##############################################################################
# tests/test_apply_review_v3.py
#
# PURPOSE: Prove the v3 review loader: it uses the v3 sentence numbers (not splitting after
#          "vs."), checks every decision before writing, needs a reason when a suggestion
#          is changed, refuses a split with missing decisions, keeps the old suggestion,
#          and counts what changed. Tiny made-up cases, no database.
##############################################################################

import pytest

from evals.apply_review_v3 import build_reviewed, check_decision

ABSTRACT = "We enrolled 50 patients. Survival was 5 mg vs. 6 mg overall (P<0.01). Safety was assessed."


def case(case_id="b1", split="dev", suggested="yes", role="primary", sentence_text="We enrolled 50 patients."):
    label = {"reported": suggested, "reported_role": None, "quote": None, "notes": "", "annotator": "gpt"}
    if suggested == "yes":
        start = ABSTRACT.index(sentence_text)
        label["reported_role"] = role
        label["quote"] = {"text": sentence_text, "start": start, "end": start + len(sentence_text)}
    return {"case_id": case_id, "split": split, "nct_id": "NCT1", "abstract": ABSTRACT, "label": label}


def decision(case_id="b1", reported="yes", role="primary", sentence=2, reviewer="Vijay", reason=""):
    return {"case_id": case_id, "reviewed_reported": reported, "reviewed_role": role,
            "reviewed_sentence": sentence, "reviewer": reviewer, "reason": reason}


def test_the_sentence_number_follows_v3_and_does_not_split_after_vs():
    reviewed, _ = build_reviewed([case()], [decision(sentence=2, reason="moved")], "dev")
    quote = reviewed[0]["label"]["quote"]
    assert quote["text"] == "Survival was 5 mg vs. 6 mg overall (P<0.01)."          # one sentence, not two
    assert ABSTRACT[quote["start"]:quote["end"]] == quote["text"]
    assert reviewed[0]["label"]["review_status"] == "reviewed"


def test_the_old_suggestion_is_kept_inside_the_new_label():
    reviewed, _ = build_reviewed([case()], [decision(sentence=2, reason="moved")], "dev")
    assert reviewed[0]["label"]["previous"]["quote"]["text"] == "We enrolled 50 patients."
    assert "Vijay" in reviewed[0]["label"]["annotator"]


def test_a_changed_label_needs_a_reason():
    with pytest.raises(ValueError, match="reason"):
        check_decision(decision(reported="unclear", role=None, sentence=None), case())
    check_decision(decision(reported="unclear", role=None, sentence=None, reason="subgroup only"), case())


def test_an_unchanged_label_needs_no_reason():
    check_decision(decision(sentence=1), case())


@pytest.mark.parametrize("bad", [
    decision(reported="maybe"),
    decision(role=None),                              # a yes needs a role
    decision(role="chief"),
    decision(sentence=None),                          # a yes needs a sentence
    decision(sentence=9, reason="x"),                 # outside 1..3
    decision(sentence=0, reason="x"),
    decision(reviewer=""),
    decision(reported="no", role="primary", sentence=None, reason="x"),   # only a yes has a role
    decision(reported="unclear", role=None, sentence=2, reason="x"),      # only a yes has a sentence
])
def test_incomplete_or_inconsistent_decisions_are_rejected(bad):
    with pytest.raises(ValueError):
        check_decision(bad, case())


def test_a_split_with_a_missing_decision_is_refused_and_nothing_is_built():
    cases = [case("b1"), case("b2")]
    with pytest.raises(ValueError, match="no decision"):
        build_reviewed(cases, [decision("b1", sentence=1)], "dev")


def test_the_other_split_does_not_need_decisions():
    cases = [case("b1"), case("b2", split="held_out")]
    reviewed, _ = build_reviewed(cases, [decision("b1", sentence=1)], "dev")
    assert [c["case_id"] for c in reviewed] == ["b1"]


def test_the_summary_counts_what_changed_by_class_and_role_and_sentence():
    cases = [case("b1"), case("b2", suggested="no"), case("b3"), case("b4")]
    decisions = [
        decision("b1", sentence=1),                                                    # unchanged
        decision("b2", reported="unclear", role=None, sentence=None, reason="related"),   # no -> unclear
        decision("b3", role="secondary", sentence=1, reason="role"),                   # role changed
        decision("b4", sentence=2, reason="better sentence"),                          # sentence changed
    ]
    _, summary = build_reviewed(cases, decisions, "dev")
    assert summary["changes"][("no", "unclear")] == 1
    assert summary["changes"][("yes", "yes")] == 3
    assert summary["role_changes"] == 1 and summary["sentence_changes"] == 1


def test_undecided_rows_in_the_decisions_file_are_ignored():
    blank = {"case_id": "b2", "reviewed_reported": None, "reviewed_role": None, "reviewed_sentence": None, "reviewer": None, "reason": None}
    reviewed, _ = build_reviewed([case("b1")], [decision("b1", sentence=1), blank], "dev")
    assert len(reviewed) == 1


def test_the_annotator_names_the_reviewer_and_who_drafted_the_decision():
    drafted = decision(sentence=1)
    drafted["drafted_by"] = "ai-model"
    reviewed, _ = build_reviewed([case()], [drafted], "dev")
    annotator = reviewed[0]["label"]["annotator"]
    assert annotator.startswith("Vijay") and "drafted by ai-model" in annotator


def test_without_a_drafter_only_the_reviewer_is_named():
    reviewed, _ = build_reviewed([case()], [decision(sentence=1)], "dev")
    assert "drafted by" not in reviewed[0]["label"]["annotator"]


# --- a fresh case with no earlier label (v4) ---------------------------------------------

def test_a_case_with_no_earlier_label_needs_no_reason_and_keeps_previous_empty():
    fresh = case()
    fresh["label"] = None
    reviewed, summary = build_reviewed([fresh], [decision(sentence=2)], "dev")
    assert reviewed[0]["label"]["previous"] is None
    assert reviewed[0]["label"]["quote"]["text"].startswith("Survival was 5 mg vs.")
    assert summary["changes"][(None, "yes")] == 1
