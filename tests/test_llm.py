##############################################################################
# tests/test_llm.py
#
# PURPOSE: Prove the model wrapper: a good answer is returned with its token counts,
#          temporary errors are retried and others are not, every failure comes back
#          as a result, and a missing key never crashes. A fake client is used,
#          so there is no network, no key and no cost.
##############################################################################

from types import SimpleNamespace

import httpx
import openai
import pytest

from trialtrace.config import Settings
from trialtrace.llm import call_llm, is_temporary


@pytest.fixture
def settings(monkeypatch):
    """Settings that ignore the real .env, so tests never depend on your key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


# --- a fake client -------------------------------------------------------------

def good_response(text="hello", tokens=(10, 5)):
    usage = None
    if tokens is not None:
        usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
    message = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)


class FakeClient:
    """Each call takes the next item: an error is raised, anything else is returned."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def status_error(error_class, code):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(code, request=request)
    return error_class("error", response=response, body=None)


def timeout_error():
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))


def run(outcomes, settings):
    """Call the wrapper with a fake client; return the result, the client and the waits."""
    client = FakeClient(outcomes)
    waits = []
    result = call_llm("my prompt", settings, client=client, sleep=waits.append)
    return result, client, waits


# --- a good answer ---------------------------------------------------------------

def test_a_good_answer_is_returned_with_model_tokens_and_time(settings):
    result, client, waits = run([good_response("hello", (10, 5))], settings)
    assert result.status == "ok" and result.text == "hello"
    assert result.model == "gpt-test"
    assert (result.input_tokens, result.output_tokens) == (10, 5)
    assert result.attempts == 1 and result.error_code is None
    assert waits == []


def test_the_prompt_the_model_and_temperature_zero_are_sent(settings):
    _, client, _ = run([good_response()], settings)
    sent = client.calls[0]
    assert sent["messages"] == [{"role": "user", "content": "my prompt"}]
    assert sent["model"] == settings.model_name
    assert sent["temperature"] == 0


def test_missing_token_counts_stay_unknown_not_zero(settings):
    result, _, _ = run([good_response(tokens=None)], settings)
    assert result.status == "ok"
    assert result.input_tokens is None and result.output_tokens is None


def test_the_model_name_comes_from_openai_chat_model(monkeypatch):
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "my-model")
    assert Settings(_env_file=None).model_name == "my-model"


# --- retries -------------------------------------------------------------------

def test_a_temporary_error_is_retried_then_succeeds(settings):
    result, client, waits = run([timeout_error(), good_response()], settings)
    assert result.status == "ok" and result.attempts == 2
    assert len(client.calls) == 2
    assert waits == [1]


def test_a_rate_limit_and_a_server_error_are_retried_with_growing_waits(settings):
    errors = [status_error(openai.RateLimitError, 429), status_error(openai.InternalServerError, 500)]
    result, _, waits = run(errors + [good_response()], settings)
    assert result.status == "ok" and result.attempts == 3
    assert waits == [1, 2]


def test_retries_stop_after_three_attempts(settings):
    result, client, waits = run([timeout_error(), timeout_error(), timeout_error()], settings)
    assert result.status == "failed" and result.error_code == "APITimeoutError"
    assert result.attempts == 3 and len(client.calls) == 3
    assert waits == [1, 2]


def test_a_bad_key_is_not_retried(settings):
    result, client, waits = run([status_error(openai.AuthenticationError, 401)], settings)
    assert result.status == "failed" and result.error_code == "AuthenticationError"
    assert result.attempts == 1 and len(client.calls) == 1
    assert waits == []


def test_is_temporary_sorts_the_error_types(settings):
    assert is_temporary(timeout_error())
    assert is_temporary(status_error(openai.RateLimitError, 429))
    assert is_temporary(status_error(openai.InternalServerError, 503))
    assert not is_temporary(status_error(openai.BadRequestError, 400))
    assert not is_temporary(status_error(openai.AuthenticationError, 401))
    assert not is_temporary(ValueError("not an api error"))


# --- failures come back as results -----------------------------------------------

def test_no_api_key_is_a_failed_result_not_a_crash(settings):
    result = call_llm("my prompt", settings)
    assert result.status == "failed" and result.error_code == "no_api_key"
    assert result.text is None


@pytest.mark.parametrize("text", [None, "", "   \n"])
def test_an_empty_answer_is_a_failed_result(settings, text):
    result, _, _ = run([good_response(text)], settings)
    assert result.status == "failed" and result.error_code == "empty_answer"


def test_a_response_with_no_choices_is_a_failed_result(settings):
    response = SimpleNamespace(choices=[], model="gpt-test", usage=None)
    result, _, _ = run([response], settings)
    assert result.status == "failed" and result.error_code == "empty_answer"


# --- temperature is optional ---------------------------------------------------------

def test_the_temperature_is_left_out_when_it_is_none(settings):
    settings.llm_temperature = None
    _, client, _ = run([good_response()], settings)
    assert "temperature" not in client.calls[0]


def test_a_chosen_temperature_is_sent(settings):
    settings.llm_temperature = 0.2
    _, client, _ = run([good_response()], settings)
    assert client.calls[0]["temperature"] == 0.2
