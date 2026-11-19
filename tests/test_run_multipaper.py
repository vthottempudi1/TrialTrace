##############################################################################
# tests/test_run_multipaper.py
#
# PURPOSE: Prove the label file is read strictly (a half-filled or inconsistent line stops the run)
#          and that the outcome-level truth is yes > unclear > no. No database.
##############################################################################

import pytest

from evals.run_multipaper import read_labels, truth_for_outcome


def write(tmp_path, *lines):
    path = tmp_path / "labels.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_good_lines_are_read_and_comments_skipped(tmp_path):
    path = write(tmp_path, "# a comment", "m01-1 | yes | primary | 8,9 | note", "m02-1 | no | | | not mentioned", "m03-1 | unclear | | | narrower")
    labels = read_labels(path)
    assert labels["m01-1"] == {"answer": "yes", "role": "primary", "sentences": [8, 9], "note": "note"}
    assert labels["m02-1"]["answer"] == "no" and labels["m03-1"]["answer"] == "unclear"


@pytest.mark.parametrize("line", [
    "m01-1 | ? | | |",                               # not filled in
    "m01-1 | yes | | 8 | no role",                   # yes needs a role
    "m01-1 | yes | primary | | no sentence",         # yes needs a sentence
    "m01-1 | no | primary | | role on a no",         # role only for yes
    "m01-1 | yes | boss | 8 | bad role",
    "m01-1 | yes",                                   # too few parts
])
def test_bad_lines_stop_the_run(tmp_path, line):
    with pytest.raises(ValueError):
        read_labels(write(tmp_path, line))


def test_truth_is_yes_then_unclear_then_no():
    assert truth_for_outcome(["no", "yes"]) == "yes"
    assert truth_for_outcome(["no", "unclear"]) == "unclear"
    assert truth_for_outcome(["no", "no"]) == "no"


def test_totals_add_counts_and_merge_the_confusion_tables():
    from evals.run_multipaper import add_up
    total = add_up([
        {"outcomes": 5, "truth_yes": 2, "confusion": {"yes -> identified": 2, "no -> not_identified": 3}, "details": [1]},
        {"outcomes": 6, "truth_yes": 0, "confusion": {"no -> not_identified": 6}, "details": [2]},
    ])
    assert total["outcomes"] == 11 and total["truth_yes"] == 2
    assert total["confusion"] == {"yes -> identified": 2, "no -> not_identified": 9}
    assert "details" not in total
