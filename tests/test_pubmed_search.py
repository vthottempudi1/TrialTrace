##############################################################################
# tests/test_pubmed_search.py
#
# PURPOSE: Prove every outcome of a PubMed search maps to the right status,
#          that a failure is never reported as "no papers", that the secret
#          key is never stored, and that the query is built safely.
#          A fake network replaces PubMed, so tests are fast and offline.
##############################################################################

import json

import httpx
import pytest

from trialtrace.config import Settings
from trialtrace.sources.pubmed import search_pubmed

NCT = "NCT04644237"


def answer(ids, count=None):
    """What PubMed's search sends back."""
    return httpx.Response(200, content=json.dumps(
        {"esearchresult": {"count": str(len(ids) if count is None else count), "idlist": ids}}).encode())


def search_with(handler, settings=None):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return search_pubmed(http, settings or Settings(_env_file=None), NCT)


def test_papers_found_are_returned_as_text_ids():
    result = search_with(lambda request: answer(["38234567", "37891234"]))
    assert result.status == "ok" and result.pmids == ["38234567", "37891234"]
    assert result.total_count == 2 and result.truncated is False and result.error_code is None


def test_no_hits_is_an_answer_not_a_failure():
    result = search_with(lambda request: answer([]))
    assert result.status == "not_found" and result.pmids == [] and result.total_count == 0
    assert result.error_code is None


def test_more_papers_than_kept_is_flagged_as_truncated():
    result = search_with(lambda request: answer(["1", "2"], count=120))
    assert result.truncated is True and result.total_count == 120 and len(result.pmids) == 2


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_server_error_is_unavailable_never_no_papers(code):
    result = search_with(lambda request: httpx.Response(code))
    assert result.status == "unavailable" and result.error_code == f"http_{code}" and result.pmids == []


def test_a_timeout_is_unavailable():
    def slow(request):
        raise httpx.ReadTimeout("too slow")
    result = search_with(slow)
    assert result.status == "unavailable" and result.error_code == "timeout"


def test_a_network_error_is_unavailable():
    def down(request):
        raise httpx.ConnectError("no connection")
    result = search_with(down)
    assert result.status == "unavailable" and result.error_code == "network_error"


@pytest.mark.parametrize("body", [
    b"<html>maintenance</html>",                                        # not JSON
    b'{"unexpected": 1}',                                               # missing fields
    b'{"esearchresult": {"count": "x", "idlist": []}}',                 # count is not a number
    b'{"esearchresult": {"count": "1", "idlist": 5}}',                  # idlist is not a list
])
def test_an_answer_in_the_wrong_shape_is_invalid_not_no_papers(body):
    result = search_with(lambda request: httpx.Response(200, content=body))
    assert result.status == "invalid" and result.error_code == "unexpected_response" and result.pmids == []


def test_a_pubmed_error_message_is_invalid():
    body = json.dumps({"esearchresult": {"ERROR": "Invalid query"}}).encode()
    result = search_with(lambda request: httpx.Response(200, content=body))
    assert result.status == "invalid" and result.error_code == "pubmed_error"


def test_the_query_names_the_trial_and_respects_the_limit():
    seen = {}

    def spy(request):
        seen.update(dict(request.url.params))
        return answer([])

    result = search_with(spy, Settings(_env_file=None, pubmed_max_results=7))
    assert seen["term"] == f'"{NCT}"[All Fields]' and seen["retmax"] == "7" and seen["db"] == "pubmed"
    assert result.query == f'"{NCT}"[All Fields]'


def test_the_secret_key_is_sent_but_never_stored_in_the_result(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "secret-key-123")        # the key comes from the environment
    seen = {}

    def spy(request):
        seen.update(dict(request.url.params))
        return answer([])

    result = search_with(spy)
    assert seen["api_key"] == "secret-key-123"
    assert "secret-key-123" not in result.model_dump_json()


def test_no_key_means_none_is_sent():
    seen = {}

    def spy(request):
        seen.update(dict(request.url.params))
        return answer([])

    search_with(spy)
    assert "api_key" not in seen


@pytest.mark.parametrize("bad_id", ["NCT123", "nct04644237", "NCT04644237 OR cancer", '"; DROP', ""])
def test_a_bad_id_is_refused_before_any_request(bad_id):
    def must_not_be_called(request):
        raise AssertionError("no request should be sent")

    with httpx.Client(transport=httpx.MockTransport(must_not_be_called)) as http:
        with pytest.raises(ValueError):
            search_pubmed(http, Settings(_env_file=None), bad_id)
