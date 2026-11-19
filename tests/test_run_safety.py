##############################################################################
# tests/test_run_safety.py
#
# PURPOSE: Prove the safety scorer's own rules (evals/run_safety.py): label lines are read strictly,
#          matching is ONE TO ONE (a baseline statement with several numbers earns one label only),
#          event names match by a shared word, the main number must be equal, and an empty count is
#          "not tested", never a pass. No database, no model.
##############################################################################

import pytest

from evals.run_safety import fits, keep_rule, match_one_to_one, parse_labels, rate_text, wilson


def label(**fields):
    base = {"pmid": "111", "sentence": 4, "kind": "serious", "term": None, "group": "drug", "count": 42.0,
            "denominator": 101.0, "percent": 41.6, "grade": None, "related": False, "category": "safety_consistent"}
    base.update(fields)
    return base


def model(**fields):
    base = {"system": "llm", "pmid": "111", "sentence": 4, "kind": "serious", "term": None, "negated": False,
            "group": "drug", "count": 42, "denominator": 101, "percent": 41.6, "grade": None, "related": False,
            "category": "safety_consistent"}
    base.update(fields)
    return base


def baseline(numbers, **fields):
    base = {"system": "baseline", "pmid": "111", "sentence": 4, "kind": "serious", "term": None, "negated": False, "numbers": numbers}
    base.update(fields)
    return base


def test_label_lines_are_read(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text(
        "# header\n"
        "111 | 4 | event | nausea | drug group | 30 | 101 | 29.7 | grade 3 | y | safety_not_comparable\n"
        "222 | none\n", encoding="utf-8")
    labels, empty = parse_labels(path)
    assert empty == ["222"]
    assert labels[0]["term"] == "nausea" and labels[0]["count"] == 30.0 and labels[0]["related"] is True
    assert labels[0]["grade"] == "grade 3" and labels[0]["sentence"] == 4


def test_a_bad_label_line_stops_the_scorer(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("111 | 4 | event | nausea | - | 30 | - | - | - | n | consistent\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_labels(path)


def test_same_statement_fits_and_other_paper_sentence_or_kind_do_not():
    assert fits(label(), model())
    assert not fits(label(), model(pmid="222"))
    assert not fits(label(), model(sentence=5))
    assert not fits(label(), model(kind="death"))


def test_the_main_number_is_the_count_when_the_label_has_one():
    assert not fits(label(), model(count=40))
    assert not fits(label(), model(count=None))                         # the percent alone is not enough
    assert fits(label(count=None), model(count=None))                   # percent-only label: the percent decides
    assert not fits(label(count=None), model(count=None, percent=45.0))


def test_event_names_match_by_a_shared_word():
    nausea = label(kind="event", term="infusion reactions (acute)")
    assert fits(nausea, model(kind="event", term="acute infusion reactions"))
    assert not fits(nausea, model(kind="event", term="serious infections"))
    assert not fits(label(kind="event", term="adverse events"), model(kind="event", term="thromboembolic events"))   # only filler words shared


def test_matching_is_one_to_one():
    labels = [label(group="drug", count=6.0), label(group="placebo", count=4.0)]
    two = [model(count=6), model(count=4)]
    assert match_one_to_one(labels, two) == {0: 0, 1: 1}
    twice = [model(count=6)]
    assert match_one_to_one([label(count=6.0), label(count=6.0)], twice) == {0: 0}      # one statement, one label


def test_a_baseline_statement_with_several_numbers_earns_one_label():
    labels = [label(group="drug", count=6.0, kind="death"), label(group="placebo", count=4.0, kind="death")]
    statements = [baseline(["6", "4"], kind="death")]
    assert match_one_to_one(labels, statements) == {0: 0}


def test_baseline_numbers_keep_their_role():
    assert fits(label(count=None, percent=21.0), baseline(["21%"]))
    assert not fits(label(count=None, percent=21.0), baseline(["21"]))
    assert fits(label(count=0.0), baseline([], negated=True))                        # "no deaths"


def test_wilson_and_not_tested():
    low, high = wilson(22, 26)
    assert round(100 * low) == 66 and round(100 * high) == 94
    assert wilson(0, 0) is None
    assert rate_text(0, 0) == "not tested (0 cases)"
    assert rate_text(22, 26).startswith("22/26 (85%")


def test_keep_rule_gates():
    result = {
        "labels": 50, "llm": {"matched": 40, "statements": 45, "said": {"safety_consistent": 0}},
        "baseline": {"matched": 20}, "false_consistent": 0, "differences": 0, "differences_routed": 0,
    }
    gates = keep_rule(result)
    assert gates["1 recall at least 10 points above the baseline"] == "met"          # 80% against 40%
    assert gates["2 precision at least 80%"] == "met"                                 # 40 of 45
    assert "never answered consistent" in gates["3 no false consistent"]
    assert gates["4 no labelled difference misses review"] == "not tested"            # no labelled difference: never "met"

    result["differences"], result["differences_routed"] = 2, 1
    result["false_consistent"] = 1
    gates = keep_rule(result)
    assert gates["3 no false consistent"].startswith("not met")
    assert gates["4 no labelled difference misses review"] == "not met"
