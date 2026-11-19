##############################################################################
# tests/test_evals_run.py
#
# PURPOSE: Prove the scoring follows the rubric: identification counts only yes/no cases,
#          "ambiguous" is never correct, role needs an identified answer, unsupported
#          quotes are caught, unclear cases are reported not rewarded, and an unlabelled
#          case stops the scoring. Tiny made-up cases, no database.
##############################################################################

import pytest

from evals.run import baseline_answer, is_unsupported, quote_agrees, score

ABSTRACT = "We enrolled 50 patients. The primary end point was overall survival (OS). Safety was assessed."


def case(case_id, reported, role=None, measure="Overall Survival (OS)", abstract=ABSTRACT):
    label = None if reported is None else {"reported": reported, "reported_role": role}
    return {"case_id": case_id, "split": "dev", "nct_id": "NCT04644237", "registry_content_hash": "a" * 64,
            "paper": {"pmid": "1"}, "abstract": abstract, "label": label,
            "registered": {"role": "primary", "position": 0, "measure_text": measure, "time_frame": None}}


def said(status, role=None, name="overall survival", quote=None):
    return {"status": status, "reported_role": role, "matched_name": name, "quote": quote}


def quote_of(sentence):
    """A correct quote: the sentence with its true position in the abstract."""
    start = ABSTRACT.index(sentence)
    return {"text": sentence, "start": start, "end": start + len(sentence)}


GOOD_QUOTE = quote_of("The primary end point was overall survival (OS).")


def test_the_baseline_answers_one_case_from_its_stored_text():
    answer = baseline_answer(case("c1", "yes", "primary"))
    assert answer["status"] == "identified" and answer["reported_role"] == "primary"
    assert answer["quote"]["text"] == "The primary end point was overall survival (OS)."


def test_identification_counts_only_yes_and_no_cases():
    cases = [case("c1", "yes"), case("c2", "no"), case("c3", "unclear")]
    answers = {"c1": said("identified", quote=GOOD_QUOTE), "c2": said("not_identified"), "c3": said("identified", quote=GOOD_QUOTE)}
    result = score(cases, answers)
    assert result["M1_identification"] == {"correct": 2, "of": 2}
    assert result["M5_on_unclear_cases"] == {"identified": 1}            # reported, never rewarded


def test_ambiguous_and_no_text_are_never_correct():
    cases = [case("c1", "yes"), case("c2", "no")]
    result = score(cases, {"c1": said("ambiguous"), "c2": said("no_text")})
    assert result["M1_identification"] == {"correct": 0, "of": 2}


def test_role_needs_an_identified_answer_with_the_same_role():
    cases = [case("c1", "yes", "primary"), case("c2", "yes", "not_stated"), case("c3", "yes", "primary"), case("c4", "yes", "primary")]
    answers = {
        "c1": said("identified", "primary", quote=GOOD_QUOTE),
        "c2": said("identified", None, quote=GOOD_QUOTE),            # not_stated equals no cue
        "c3": said("identified", "secondary", quote=GOOD_QUOTE),     # wrong role
        "c4": said("not_identified"),                                # abstaining is not correct
    }
    assert score(cases, answers)["M2_role"] == {"correct": 2, "of": 4}


def test_a_false_identified_is_counted():
    result = score([case("c1", "no")], {"c1": said("identified", quote=GOOD_QUOTE)})
    assert result["M4_false_identified"] == 1


def test_an_invented_quote_is_an_unsupported_claim():
    fake = {"text": "invented sentence", "start": 0, "end": 17}
    assert is_unsupported(case("c1", "yes"), said("identified", quote=fake))


def test_a_quote_that_does_not_name_the_outcome_is_unsupported():
    safety = quote_of("Safety was assessed.")                  # really there, but does not name the outcome
    assert is_unsupported(case("c1", "yes"), said("identified", quote=safety))


def test_an_identified_answer_with_no_quote_is_unsupported():
    assert is_unsupported(case("c1", "yes"), said("identified", quote=None))


def test_a_real_quote_is_supported_and_other_statuses_are_never_unsupported():
    assert not is_unsupported(case("c1", "yes"), said("identified", quote=GOOD_QUOTE))
    assert not is_unsupported(case("c1", "yes"), said("not_identified"))


def test_failures_are_counted_not_dropped():
    answers = {"c1": {"status": "failed", "reported_role": None, "matched_name": None, "quote": None, "error": "ValueError"}}
    assert score([case("c1", "yes")], answers)["M6_failures"] == 1


def test_an_unlabelled_case_stops_the_scoring():
    with pytest.raises(ValueError):
        score([case("c1", None)], {"c1": said("not_identified")})


# --- M3b: is it the sentence the labeller picked? ------------------------------

def yes_case_with_labelled_quote(sentence):
    c = case("c1", "yes", "primary")
    c["label"]["quote"] = quote_of(sentence)
    return c


def test_the_same_sentence_as_the_label_agrees():
    c = yes_case_with_labelled_quote("The primary end point was overall survival (OS).")
    assert quote_agrees(c, said("identified", quote=GOOD_QUOTE))


def test_a_real_but_different_sentence_does_not_agree_even_though_it_is_a_valid_quote():
    c = yes_case_with_labelled_quote("Safety was assessed.")
    answer = said("identified", quote=GOOD_QUOTE)
    assert not is_unsupported(c, answer) and not quote_agrees(c, answer)       # passes M3, fails M3b


def test_only_identified_answers_can_agree():
    c = yes_case_with_labelled_quote("The primary end point was overall survival (OS).")
    assert not quote_agrees(c, said("ambiguous", quote=GOOD_QUOTE))
    assert not quote_agrees(c, said("not_identified"))


def test_the_score_reports_quote_agreement_over_the_yes_cases():
    agreeing = yes_case_with_labelled_quote("The primary end point was overall survival (OS).")
    other = yes_case_with_labelled_quote("Safety was assessed.")
    other["case_id"] = "c2"
    result = score([agreeing, other], {"c1": said("identified", "primary", quote=GOOD_QUOTE), "c2": said("identified", "primary", quote=GOOD_QUOTE)})
    assert result["M3b_quote_agreement"] == {"correct": 1, "of": 2} and result["M3_bad_quotes"] == 0


# --- the LLM system in the eval ----------------------------------------------------

import json
from types import SimpleNamespace

from evals.run import llm_answer, usage_of
from trialtrace.config import Settings


class FakeModel:
    """A fake OpenAI client that always replies with the same text."""

    def __init__(self, reply):
        self.reply = reply
        message = SimpleNamespace(content=reply)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: self.response))


def reply_json(status="identified", quote="The primary end point was overall survival (OS)."):
    return json.dumps({"status": status, "name": "overall survival", "quote": quote, "role": "primary"})


def test_the_llm_answer_has_the_same_shape_as_the_baseline_answer():
    answer = llm_answer(case("c1", "yes", "primary"), Settings(_env_file=None), FakeModel(reply_json()))
    assert answer["status"] == "identified" and answer["reported_role"] == "primary"
    assert answer["quote"]["text"] == "The primary end point was overall survival (OS)."
    assert answer["llm"]["model"] == "gpt-test" and "error" not in answer


def test_a_rejected_llm_answer_is_a_failure_that_m6_counts():
    answer = llm_answer(case("c1", "yes", "primary"), Settings(_env_file=None), FakeModel(reply_json(quote="invented")))
    assert answer["status"] == "failed" and answer["error"] == "quote_not_found"
    assert score([case("c1", "yes", "primary")], {"c1": answer})["M6_failures"] == 1


def test_usage_adds_up_the_model_calls_and_ignores_the_baseline():
    with_llm = llm_answer(case("c1", "yes", "primary"), Settings(_env_file=None), FakeModel(reply_json()))
    totals = usage_of({"c1": with_llm, "c2": said("not_identified")})
    assert totals["calls"] == 1 and totals["input_tokens"] == 100 and totals["output_tokens"] == 20


def test_overclaiming_counts_identified_answers_on_unclear_cases_only():
    cases = [case("c1", "unclear"), case("c2", "unclear"), case("c3", "unclear"), case("c4", "no")]
    answers = {
        "c1": said("identified", quote=GOOD_QUOTE),      # overclaim
        "c2": said("ambiguous"),                          # the only real abstention
        "c3": said("not_identified"),                     # a definite answer, but not an overclaim
        "c4": said("identified", quote=GOOD_QUOTE),      # counts in M4, not M4b
    }
    result = score(cases, answers)
    assert result["M4b_overclaim_on_unclear"] == 1 and result["M4_false_identified"] == 1


def test_recall_precision_and_f1():
    # 3 yes cases: 2 found, 1 missed. Also identified wrongly on a no case and an unclear case.
    cases = [case("c1", "yes"), case("c2", "yes"), case("c3", "yes"), case("c4", "no"), case("c5", "unclear")]
    answers = {
        "c1": said("identified", quote=GOOD_QUOTE), "c2": said("identified", quote=GOOD_QUOTE), "c3": said("ambiguous"),
        "c4": said("identified", quote=GOOD_QUOTE), "c5": said("identified", quote=GOOD_QUOTE),
    }
    result = score(cases, answers)
    assert result["recall"] == {"correct": 2, "of": 3, "value": 2 / 3}
    assert result["precision"] == {"correct": 2, "of": 4, "value": 0.5}
    assert result["f1"] == pytest.approx(2 * (2 / 3) * 0.5 / ((2 / 3) + 0.5))


def test_recall_and_precision_are_not_defined_without_cases_or_answers():
    result = score([case("c1", "no")], {"c1": said("not_identified")})
    assert result["recall"]["value"] is None and result["precision"]["value"] is None and result["f1"] is None
