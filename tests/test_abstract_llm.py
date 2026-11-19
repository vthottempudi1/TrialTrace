##############################################################################
# tests/test_abstract_llm.py
#
# PURPOSE: Prove the LLM abstract check: the model's JSON is read, "unclear" becomes
#          "ambiguous", the quote's position is worked out by code, and every bad
#          answer (invented quote, wrong name, broken JSON, model failure) becomes
#          `failed` with a reason, never a guess. A fake client is used: no network, no key.
##############################################################################

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from trialtrace.compare.abstract_llm import match_with_llm, read_json
from trialtrace.config import Settings
from trialtrace.contracts import RegisteredOutcome
from trialtrace.parse.pubmed_parser import Paper

ABSTRACT = "We enrolled 50 patients. The primary end point was overall survival (OS). Safety was assessed."
QUOTE = "The primary end point was overall survival (OS)."


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


def outcome(measure="Overall Survival (OS)"):
    return RegisteredOutcome(nct_id="NCT04644237", content_hash="a" * 64, role="primary", position=0,
                             measure_text=measure, time_frame="5 years")


def paper(abstract=ABSTRACT):
    return Paper(pmid="1", abstract=abstract, listed_nct_ids=["NCT04644237"])


# --- a fake client that replies with a chosen text -----------------------------

class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        message = SimpleNamespace(content=self.reply)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)


def answer(status="identified", name="overall survival", quote=QUOTE, role="primary"):
    return json.dumps({"status": status, "name": name, "quote": quote, "role": role})


def run(reply, settings, the_paper=None):
    client = FakeClient(reply)
    result = match_with_llm(outcome(), the_paper or paper(), settings, client=client, sleep=lambda seconds: None)
    return result, client


# --- identified ----------------------------------------------------------------

def test_an_identified_answer_gets_its_position_from_code(settings):
    result, _ = run(answer(), settings)
    assert result.status == "identified" and result.reported_role == "primary"
    assert result.matched_name == "overall survival"
    start = ABSTRACT.index(QUOTE)
    assert (result.quote.start, result.quote.end) == (start, start + len(QUOTE))
    assert ABSTRACT[result.quote.start:result.quote.end] == QUOTE
    assert result.llm.model == "gpt-test" and result.llm.input_tokens == 10


def test_the_prompt_holds_the_outcome_the_time_frame_and_the_abstract(settings):
    _, client = run(answer(), settings)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Overall Survival (OS)" in prompt and "5 years" in prompt and ABSTRACT in prompt


def test_not_stated_and_invented_roles_become_no_role(settings):
    assert run(answer(role="not_stated"), settings)[0].reported_role is None
    assert run(answer(role="chief"), settings)[0].reported_role is None


def test_json_inside_a_code_fence_is_read(settings):
    fenced = "```json\n" + answer() + "\n```"
    assert run(fenced, settings)[0].status == "identified"


# --- not identified and unclear -------------------------------------------------

def test_not_identified_is_kept_with_no_quote(settings):
    result, _ = run(answer("not_identified", None, None, None), settings)
    assert result.status == "not_identified" and result.quote is None and result.error_code is None


def test_unclear_becomes_ambiguous(settings):
    result, _ = run(answer("unclear", None, None, None), settings)
    assert result.status == "ambiguous" and result.quote is None


# --- code rejects bad answers -----------------------------------------------------

def test_an_invented_quote_is_rejected(settings):
    result, _ = run(answer(quote="Overall survival was great."), settings)
    assert result.status == "failed" and result.error_code == "quote_not_found"
    assert result.quote is None


def test_a_quote_with_one_changed_character_is_rejected(settings):
    result, _ = run(answer(quote=QUOTE.replace("end point", "end-point")), settings)
    assert result.error_code == "quote_not_found"


def test_a_real_quote_that_does_not_contain_the_claimed_name_is_rejected(settings):
    result, _ = run(answer(quote="Safety was assessed."), settings)
    assert result.status == "failed" and result.error_code == "name_not_in_quote"


@pytest.mark.parametrize("name,quote,error_code", [
    (None, QUOTE, "missing_name"),
    ("overall survival", None, "missing_quote"),
    ("", QUOTE, "empty_name"),
    ("   ", QUOTE, "empty_name"),
    ("overall survival", "", "empty_quote"),
    ("overall survival", "   ", "empty_quote"),
    (5, QUOTE, "invalid_name_type"),
    ([], QUOTE, "invalid_name_type"),
    ("overall survival", 5, "invalid_quote_type"),
    ("overall survival", {}, "invalid_quote_type"),
])
def test_identified_without_a_name_or_quote_is_rejected(settings, name, quote, error_code):
    result, _ = run(answer(name=name, quote=quote), settings)
    assert result.status == "failed" and result.error_code == error_code


@pytest.mark.parametrize("reply", ["not json at all", "[1, 2]", "", "null"])
def test_broken_json_is_rejected(settings, reply):
    result, _ = run(reply, settings)
    assert result.status == "failed" and result.error_code in ("bad_json", "empty_answer")


def test_an_unknown_status_is_rejected(settings):
    result, _ = run(answer(status="maybe"), settings)
    assert result.status == "failed" and result.error_code == "bad_status"


# --- model failures and no text ----------------------------------------------------

def test_a_model_failure_is_a_failed_result_with_its_reason(settings):
    request = httpx.Request("POST", "https://api.openai.com")
    error = openai.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    result, client = run(error, settings)
    assert result.status == "failed" and result.error_code == "AuthenticationError"
    assert len(client.calls) == 1                                  # a bad key is not retried


def test_no_api_key_is_a_failed_result(settings):
    result = match_with_llm(outcome(), paper(), settings)
    assert result.status == "failed" and result.error_code == "no_api_key"


@pytest.mark.parametrize("abstract", [None, "", "   "])
def test_no_abstract_means_no_text_and_the_model_is_not_called(settings, abstract):
    result, client = run(answer(), settings, the_paper=paper(abstract))
    assert result.status == "no_text" and client.calls == []


# --- read_json ------------------------------------------------------------------

def test_read_json_returns_a_dict_or_none():
    assert read_json('{"a": 1}') == {"a": 1}
    assert read_json("nope") is None
    assert read_json("[1]") is None


# --- known limit: a repeated sentence ---------------------------------------------

def test_a_repeated_sentence_gets_the_position_of_its_first_occurrence(settings):
    repeated = QUOTE + " " + QUOTE
    result, _ = run(answer(), settings, the_paper=paper(repeated))
    assert result.status == "identified"
    assert (result.quote.start, result.quote.end) == (0, len(QUOTE))       # the first one, always


# --- abbreviations the abstract defines ----------------------------------------------

DEFINED = "We studied overall survival (OS). At 24 months, OS was 78%. Safety was assessed."


def test_an_abbreviation_the_abstract_defines_for_the_registered_outcome_is_accepted(settings):
    reply = answer(name="overall survival (OS)", quote="At 24 months, OS was 78%.")
    result, _ = run(reply, settings, the_paper=paper(DEFINED))
    assert result.status == "identified"
    assert result.matched_name == "OS"                                # the name that really sits in the quote
    assert DEFINED[result.quote.start:result.quote.end] == "At 24 months, OS was 78%."


def test_an_abbreviation_the_abstract_never_defines_is_rejected(settings):
    undefined = "At 24 months, OS was 78%. Safety was assessed."
    result, _ = run(answer(name="overall survival (OS)", quote="At 24 months, OS was 78%."), settings, the_paper=paper(undefined))
    assert result.status == "failed" and result.error_code == "name_not_in_quote"


def test_an_abbreviation_defined_for_a_different_outcome_is_rejected(settings):
    other = "We studied progression-free survival (PFS). PFS was 5 months."
    result, _ = run(answer(name="overall survival (PFS)", quote="PFS was 5 months."), settings, the_paper=paper(other))
    assert result.status == "failed" and result.error_code == "name_not_in_quote"


def test_a_defined_abbreviation_does_not_excuse_a_quote_that_lacks_it(settings):
    result, _ = run(answer(name="overall survival (OS)", quote="Safety was assessed."), settings, the_paper=paper(DEFINED))
    assert result.status == "failed" and result.error_code == "name_not_in_quote"


# --- spaces: a non-breaking space in the abstract ----------------------------------------

def test_a_quote_with_a_normal_space_matches_an_abstract_with_a_non_breaking_space(settings):
    abstract = "We enrolled 50 patients. The primary end point was overall survival at 12\u00a0weeks (OS)."
    model_quote = "The primary end point was overall survival at 12 weeks (OS)."          # normal space
    result, _ = run(answer(quote=model_quote), settings, the_paper=paper(abstract))
    assert result.status == "identified"
    assert result.quote.text == "The primary end point was overall survival at 12\u00a0weeks (OS)."   # the original text
    assert abstract[result.quote.start:result.quote.end] == result.quote.text


def test_the_prompt_asks_the_decision_in_order_and_the_quote_before_the_name(settings):
    _, client = run(answer(), settings)
    prompt = client.calls[0]["messages"][0]["content"]
    assert prompt.index('"quote"') < prompt.index('"name"')
    assert "planned or future endpoints" in prompt


# --- the kind of paper: code does not accept a result from a protocol or a review -----------

def with_document(document, **kwargs):
    reply = json.loads(answer(**kwargs))
    reply["document"] = document
    return json.dumps(reply)


@pytest.mark.parametrize("document", ["protocol", "review"])
def test_an_identified_answer_from_a_protocol_or_review_becomes_not_identified(settings, document):
    result, _ = run(with_document(document), settings)
    assert result.status == "not_identified" and result.quote is None
    assert result.reason == f"paper is a {document}"


@pytest.mark.parametrize("document", ["trial_results", "other", None])
def test_other_paper_types_are_accepted_as_before(settings, document):
    result, _ = run(with_document(document), settings)
    assert result.status == "identified"


def test_the_prompt_asks_for_the_paper_type_first(settings):
    _, client = run(answer(), settings)
    prompt = client.calls[0]["messages"][0]["content"]
    assert prompt.index('"document"') < prompt.index('"status"')


def test_outcome_prompt_explicitly_identifies_the_target_in_a_multi_trial_paper(settings):
    text = "NCT04644237 and NCT90000001 were studied. Overall survival was 12 months in NCT90000001."
    client = FakeClient(answer(status="not_identified", name=None, quote=None, role=None))
    result = match_with_llm(outcome(), paper(text), settings, client=client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Target trial: NCT04644237" in prompt
    assert "separately attributable target-trial results" in prompt
    assert "Pooled results" in prompt
    assert result.status == "not_identified"
