##############################################################################
# tests/test_evals_label.py
#
# PURPOSE: Prove the two pieces of logic in the evaluation set: cases are chosen by a fixed
#          rule, and a label always carries an exact quote position, a role, and the
#          annotator, with bad answers refused. A fake keyboard replaces the person.
##############################################################################

import json

import pytest

from evals.label import load_cases, make_label, run
from evals.make_cases import choose_outcomes
from trialtrace.contracts import RegisteredOutcome

NCT = "NCT04644237"
ABSTRACT = "We enrolled 50 patients. The primary end point was survival. Safety was also assessed."


def outcome(position, role="secondary"):
    return RegisteredOutcome(nct_id=NCT, content_hash="a" * 64, role=role, position=position, measure_text=f"Outcome {position}")


# --- choosing cases ------------------------------------------------------------

def test_primary_outcomes_come_first():
    outcomes = [outcome(0, "secondary"), outcome(1, "primary"), outcome(2, "secondary"), outcome(3, "primary")]
    chosen = choose_outcomes(outcomes, quota=3, seed=1)
    assert [o.position for o in chosen[:2]] == [1, 3] and chosen[2].role == "secondary"


def test_the_same_seed_gives_the_same_choice():
    outcomes = [outcome(i) for i in range(10)]
    assert choose_outcomes(outcomes, 3, seed=7) == choose_outcomes(outcomes, 3, seed=7)


def test_the_quota_is_never_exceeded_even_with_many_primary_outcomes():
    outcomes = [outcome(i, "primary") for i in range(5)]
    assert len(choose_outcomes(outcomes, 2, seed=1)) == 2


def test_a_short_list_gives_fewer_than_the_quota_not_an_error():
    assert len(choose_outcomes([outcome(0), outcome(1)], 5, seed=1)) == 2


# --- labels --------------------------------------------------------------------

def test_a_yes_label_stores_the_exact_sentence_with_its_position():
    label = make_label("yes", "primary", 2, ABSTRACT)
    quote = label["quote"]
    assert quote["text"] == "The primary end point was survival."
    assert ABSTRACT[quote["start"]:quote["end"]] == quote["text"]
    assert label["reported_role"] == "primary" and label["annotator"].startswith("single annotator")


@pytest.mark.parametrize("answer", ["no", "unclear"])
def test_no_and_unclear_labels_have_no_role_and_no_quote(answer):
    label = make_label(answer, None, None, ABSTRACT)
    assert label["reported"] == answer and label["reported_role"] is None and label["quote"] is None


@pytest.mark.parametrize("role, sentence", [(None, 1), ("primary", None), ("primary", 0), ("primary", 9), ("first", 1)])
def test_a_yes_label_needs_a_valid_role_and_sentence(role, sentence):
    with pytest.raises(ValueError):
        make_label("yes", role, sentence, ABSTRACT)


def test_an_unknown_answer_is_refused():
    with pytest.raises(ValueError):
        make_label("maybe", None, None, ABSTRACT)


# --- the labelling session -----------------------------------------------------

def write_cases(path, count=2):
    cases = [{"case_id": f"c{i + 1:02d}", "split": "dev", "nct_id": NCT, "paper": {"pmid": "1"},
              "registered": {"role": "primary", "measure_text": "Survival", "time_frame": None},
              "abstract": ABSTRACT, "label": None} for i in range(count)]
    path.write_text("\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8")


def keyboard(*answers):
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def test_a_session_saves_each_label_and_shows_no_system_answers(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path)
    shown = []
    # case 1: yes, primary, sentence 2, no note.   case 2: no, no note.
    done = run(path=path, ask=keyboard("y", "p", "2", "", "n", ""), show=shown.append)
    cases = load_cases(path)
    assert done == 2 and cases[0]["label"]["reported"] == "yes" and cases[1]["label"]["reported"] == "no"
    text = "\n".join(shown)
    assert "[2] The primary end point was survival." in text
    assert "baseline" not in text.lower() and "identified" not in text.lower()


def test_bad_keystrokes_are_asked_again(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path, count=1)
    run(path=path, ask=keyboard("x", "", "y", "z", "p", "abc", "9", "2", ""), show=lambda _: None)
    assert load_cases(path)[0]["label"]["quote"]["text"] == "The primary end point was survival."


def test_quitting_midway_keeps_the_labels_already_given(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path)

    def quits_on_second_case(prompt, answers=iter(["n", ""])):
        return next(answers)                       # runs out during case 2: StopIteration stands in for quitting
    with pytest.raises(StopIteration):
        run(path=path, ask=quits_on_second_case, show=lambda _: None)
    cases = load_cases(path)
    assert cases[0]["label"]["reported"] == "no" and cases[1]["label"] is None


def test_only_unlabelled_cases_are_asked_next_time(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path)
    run(path=path, ask=keyboard("n", "", "n", ""), show=lambda _: None)
    assert run(path=path, ask=keyboard(), show=lambda _: None) == 0


def test_redo_keeps_the_old_label_as_history(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path, count=1)
    run(path=path, ask=keyboard("n", ""), show=lambda _: None)
    run(redo="c01", path=path, ask=keyboard("u", "was not sure"), show=lambda _: None)
    label = load_cases(path)[0]["label"]
    assert label["reported"] == "unclear" and label["notes"] == "was not sure"
    assert label["previous"]["reported"] == "no"


def test_redo_of_an_unknown_case_stops(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path, count=1)
    with pytest.raises(SystemExit):
        run(redo="c99", path=path, ask=keyboard(), show=lambda _: None)
