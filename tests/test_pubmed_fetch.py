##############################################################################
# tests/test_pubmed_fetch.py
#
# PURPOSE: Prove the paper fetch returns the XML untouched, maps every failure
#          to "unavailable", refuses unsafe or oversized requests, never keeps
#          the secret key, and that the XML can be saved by the raw store.
#          A fake network replaces PubMed, so tests are fast and offline.
##############################################################################

import httpx
import pytest

from trialtrace.config import Settings
from trialtrace.sources.pubmed import fetch_pubmed_records
from trialtrace.sources.raw_store import compute_content_hash, save_raw

NCT = "NCT04644237"
XML = b'<?xml version="1.0"?>\n<PubmedArticleSet>\n  <PubmedArticle><PMID>111</PMID></PubmedArticle>\n</PubmedArticleSet>\n'


def fetch_with(handler, pmids=("111", "222"), settings=None):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return fetch_pubmed_records(http, settings or Settings(_env_file=None), NCT, list(pmids))


def test_the_xml_comes_back_byte_for_byte():
    result = fetch_with(lambda request: httpx.Response(200, content=XML))
    assert result.status == "ok" and result.body == XML and result.error_code is None
    assert result.pmids == ["111", "222"] and result.nct_id == NCT


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_server_error_is_unavailable_with_no_body(code):
    result = fetch_with(lambda request: httpx.Response(code))
    assert result.status == "unavailable" and result.error_code == f"http_{code}" and result.body is None


def test_a_timeout_is_unavailable():
    def slow(request):
        raise httpx.ReadTimeout("too slow")
    result = fetch_with(slow)
    assert result.status == "unavailable" and result.error_code == "timeout" and result.body is None


def test_a_network_error_is_unavailable():
    def down(request):
        raise httpx.ConnectError("no connection")
    result = fetch_with(down)
    assert result.status == "unavailable" and result.error_code == "network_error"


def test_the_request_asks_for_xml_of_exactly_these_papers():
    seen = {}

    def spy(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, content=XML)

    fetch_with(spy, pmids=("111", "222", "333"))
    assert seen == {"db": "pubmed", "id": "111,222,333", "retmode": "xml"}


def test_the_secret_key_is_sent_but_not_kept_in_the_address(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "secret-key-123")
    seen = {}

    def spy(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, content=XML)

    result = fetch_with(spy, settings=Settings(_env_file=None))
    assert seen["api_key"] == "secret-key-123"
    assert "secret-key-123" not in result.url and "secret-key-123" not in result.model_dump_json()
    assert result.url.endswith("id=111,222") and "api_key" not in result.url


def test_the_saved_address_works_on_a_failure_too(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "secret-key-123")
    result = fetch_with(lambda request: httpx.Response(500))
    assert result.url.endswith("id=111,222") and "secret-key-123" not in result.url


@pytest.mark.parametrize("pmids", [
    [], ["abc"], ["12 34"], ["111,222"], ["1; DROP"], [""], ["-5"], ["1234567890"],
])
def test_missing_or_unsafe_paper_ids_are_refused_before_any_request(pmids):
    def must_not_be_called(request):
        raise AssertionError("no request should be sent")

    with httpx.Client(transport=httpx.MockTransport(must_not_be_called)) as http:
        with pytest.raises(ValueError):
            fetch_pubmed_records(http, Settings(_env_file=None), NCT, pmids)


def test_more_papers_than_one_batch_is_refused():
    settings = Settings(_env_file=None, pubmed_batch_size=3)
    ids = ["1", "2", "3", "4"]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=XML))) as http:
        with pytest.raises(ValueError):
            fetch_pubmed_records(http, settings, NCT, ids)
        assert fetch_pubmed_records(http, settings, NCT, ids[:3]).status == "ok"


def test_a_bad_trial_id_is_refused():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=XML))) as http:
        with pytest.raises(ValueError):
            fetch_pubmed_records(http, Settings(_env_file=None), "NCT12", ["111"])


# --- saving the XML with the raw store ---------------------------------------

def test_the_xml_is_saved_untouched_named_by_its_hash(tmp_path):
    result = fetch_with(lambda request: httpx.Response(200, content=XML))
    stored = save_raw(result.nct_id, result.url, result.retrieved_at, result.body, tmp_path,
                      source="pubmed", extension="xml")
    saved = tmp_path / "raw" / "pubmed" / NCT / f"{stored.content_hash}.xml"
    assert saved.read_bytes() == XML
    assert stored.content_hash == compute_content_hash(XML, "pubmed")
    assert (tmp_path / "raw" / "pubmed" / NCT / f"{stored.content_hash}.meta.json").exists()


def test_fetching_the_same_papers_twice_saves_one_file(tmp_path):
    for _ in range(2):
        result = fetch_with(lambda request: httpx.Response(200, content=XML))
        save_raw(result.nct_id, result.url, result.retrieved_at, result.body, tmp_path, source="pubmed", extension="xml")
    assert len(list((tmp_path / "raw" / "pubmed" / NCT).glob("*.xml"))) == 1


def test_changed_records_are_a_new_file_and_the_old_one_stays(tmp_path):
    changed = XML.replace(b"111", b"999")
    for body in (XML, changed):
        result = fetch_with(lambda request, body=body: httpx.Response(200, content=body))
        save_raw(result.nct_id, result.url, result.retrieved_at, result.body, tmp_path, source="pubmed", extension="xml")
    assert len(list((tmp_path / "raw" / "pubmed" / NCT).glob("*.xml"))) == 2
