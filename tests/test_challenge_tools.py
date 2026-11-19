##############################################################################
# tests/test_challenge_tools.py
#
# PURPOSE: Prove the challenge-set helpers read failure-mode tags correctly, count wrong "identified" answers per
#          confirmed failure mode, and report original agreement between two label sets.
##############################################################################

from evals.challenge_tools import cohens_kappa, compare_labels, read_tags, tally_by_mode


def test_tags_are_read_from_the_start_of_a_note():
    assert read_tags("FM=attr,subgroup; s8 pooled") == ["attr", "subgroup"]
    assert read_tags("FM=none; plain no") == [] and read_tags("no tag here") == [] and read_tags("") == []
    assert read_tags("FM=time;") == ["time"] and read_tags("FM=bare, time ; x") == ["bare", "time"]
    assert read_tags("FM=invented,subgroup; x") == ["subgroup"]                 # only the four known modes count


def pair(label, status, tags):
    return {"label": label, "status": status, "tags": tags}


def test_tally_counts_wrong_identified_per_confirmed_mode():
    pairs = [pair("unclear", "identified", ["subgroup"]), pair("unclear", "ambiguous", ["subgroup", "time"]), pair("yes", "identified", ["subgroup"]),
             pair("no", "not_identified", ["attr"]), pair("unclear", "identified", ["bare"]), pair("no", "identified", [])]
    result = tally_by_mode(pairs)
    assert result["subgroup"]["pairs"] == 3 and result["subgroup"]["wrong_identified"] == 1 and result["subgroup"]["model_identified"] == 2
    assert result["subgroup"]["labelled"] == {"yes": 1, "unclear": 2, "no": 0}
    assert result["subgroup"]["model_unclear_on_labelled_unclear"] == 1 and result["subgroup"]["labelled_unclear"] == 2
    assert result["time"]["pairs"] == 1 and result["attr"]["wrong_identified"] == 0 and result["bare"]["wrong_identified"] == 1
    assert sum(r["wrong_identified"] for r in result.values()) == 2             # the untagged wrong identified is not in any mode


def test_original_agreement_and_disagreements():
    first = {"a": "yes", "b": "no", "c": "unclear", "d": "no", "only-first": "yes"}
    second = {"a": "yes", "b": "unclear", "c": "unclear", "d": "no", "only-second": "no"}
    result = compare_labels(first, second)
    assert result["pairs"] == 4 and result["agree"] == 3 and result["share"] == 0.75
    assert result["disagreements"] == [("b", "no", "unclear")] and result["confusion"]["no -> unclear"] == 1
    assert compare_labels({}, {"a": "yes"})["share"] is None


def test_kappa_is_1_for_identical_sets_and_zero_for_chance_level():
    same = {"a": "yes", "b": "no", "c": "unclear", "d": "no"}
    assert cohens_kappa(same, dict(same)) == 1.0
    assert cohens_kappa({"a": "yes", "b": "yes", "c": "no", "d": "no"}, {"a": "yes", "b": "no", "c": "yes", "d": "no"}) == 0.0
    assert cohens_kappa({"a": "yes"}, {"a": "yes"}) is None and cohens_kappa({}, {}) is None


def test_reconciliation_needs_a_reasoned_decision_for_every_disagreement_and_changes_only_those():
    import pytest
    from evals.challenge_tools import apply_reconciliation
    drafts = {"a": "yes", "b": "no", "c": "unclear"}
    disputes = [("b", "no", "unclear")]
    with pytest.raises(ValueError, match="no decision"):
        apply_reconciliation(drafts, {}, disputes)
    with pytest.raises(ValueError, match="not in dispute"):
        apply_reconciliation(drafts, {"b": {"final": "no", "reason": "s3"}, "a": {"final": "no", "reason": "x"}}, disputes)
    with pytest.raises(ValueError, match="reason"):
        apply_reconciliation(drafts, {"b": {"final": "unclear", "reason": " "}}, disputes)
    with pytest.raises(ValueError, match="yes, no or unclear"):
        apply_reconciliation(drafts, {"b": {"final": "maybe", "reason": "s3"}}, disputes)
    final = apply_reconciliation(drafts, {"b": {"final": "unclear", "reason": "s3 gives a bare statement"}}, disputes)
    assert final == {"a": "yes", "b": "unclear", "c": "unclear"} and drafts["b"] == "no"       # the draft dict is not changed
