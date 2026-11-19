##############################################################################
# tests/test_ingest_papers.py
#
# PURPOSE: Prove the paper ingest: stores papers and verdicts, records every search
#          (so "nothing matched" differs from "could not search"), counts failed
#          batches without losing the others, keeps the raw file even when it cannot
#          be read, retries only temporary errors, and pauses before each request.
#          A fake PubMed replaces the network; the database is rolled back;
#          raw files go to a temporary folder; waiting is faked.
##############################################################################

import json

import httpx
import pytest

from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_paper_search, get_papers_for_trial
from trialtrace.ingest_papers import PAUSE_SECONDS, ingest_papers

NCT = "NCT04644237"


def article(pmid: str, listed: bool = True) -> str:
    bank = (f"<DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName><AccessionNumberList>"
            f"<AccessionNumber>{NCT}</AccessionNumber></AccessionNumberList></DataBank></DataBankList>") if listed else ""
    return (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article><ArticleTitle>Paper {pmid}</ArticleTitle>"
            f"{bank}</Article></MedlineCitation></PubmedArticle>")


class FakePubMed:
    """Answers esearch with the given IDs and efetch with a matching XML file.

    overrides: {n: response} replaces the answer to the n-th fetch request (counting retries).
    """

    def __init__(self, pmids, search_response=None, overrides=None, unlisted=()):
        self.pmids, self.overrides, self.unlisted = list(pmids), overrides or {}, set(unlisted)
        self.search_response = search_response
        self.searches = 0
        self.fetches = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "esearch" in request.url.path:
            self.searches += 1
            if self.search_response is not None:
                return self.search_response
            body = {"esearchresult": {"count": str(len(self.pmids)), "idlist": self.pmids}}
            return httpx.Response(200, content=json.dumps(body).encode())
        self.fetches += 1
        if self.fetches in self.overrides:
            return self.overrides[self.fetches]
        asked = request.url.params["id"].split(",")
        articles = "".join(article(pmid, listed=pmid not in self.unlisted) for pmid in asked)
        return httpx.Response(200, content=f"<PubmedArticleSet>{articles}</PubmedArticleSet>".encode())

    @property
    def requests(self) -> int:
        return self.searches + self.fetches


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, pubmed_batch_size=2)     # small batches: easy to split


def run(conn, settings, fake, nct_id=NCT):
    waits: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(fake)) as http:
        result = ingest_papers(conn, http, settings, nct_id, sleep=waits.append)
    return result, waits


def xml_files(settings):
    return list(settings.data_dir.rglob("*.xml"))


def test_an_invalid_id_sends_no_request_and_records_nothing(conn, settings):
    fake = FakePubMed(["111"])
    result, waits = run(conn, settings, fake, nct_id="NCT12")
    assert result.status == "invalid_id" and fake.requests == 0 and waits == []
    assert get_latest_paper_search(conn, "NCT12") is None


def test_papers_are_stored_with_their_verdict(conn, settings):
    result, _ = run(conn, settings, FakePubMed(["111", "222"]))
    assert result.status == "done" and result.papers_found == 2 and result.papers_new == 2
    rows = get_papers_for_trial(conn, NCT)
    assert sorted(r["pmid"] for r in rows) == ["111", "222"]
    assert {r["status"] for r in rows} == {"listed"}
    assert len(xml_files(settings)) == 1                                # the untouched XML was kept


def test_the_search_is_recorded_with_its_query(conn, settings):
    run(conn, settings, FakePubMed(["111", "222"]))
    search = get_latest_paper_search(conn, NCT)
    assert search["status"] == "ok" and search["pmids_found"] == 2 and search["query"] == f'"{NCT}"[All Fields]'
    assert search["batches_total"] == 1 and search["batches_failed"] == 0 and search["answered_fully"] is True


def test_never_searched_is_none(conn):
    assert get_latest_paper_search(conn, NCT) is None


def test_zero_matches_is_recorded_as_not_found(conn, settings):
    empty = httpx.Response(200, content=json.dumps({"esearchresult": {"count": "0", "idlist": []}}).encode())
    result, _ = run(conn, settings, FakePubMed([], search_response=empty))
    assert result.status == "no_papers_found"
    search = get_latest_paper_search(conn, NCT)
    assert search["status"] == "not_found" and search["answered_fully"] is True
    assert get_papers_for_trial(conn, NCT) == []


def test_a_search_that_fails_is_retried_then_recorded_as_unknown(conn, settings):
    fake = FakePubMed([], search_response=httpx.Response(500))
    result, waits = run(conn, settings, fake)
    assert result.status == "search_failed" and result.detail == "http_500"
    assert fake.searches == 3                                           # one try plus two retries
    assert 1 in waits and 2 in waits                                    # growing wait between tries
    search = get_latest_paper_search(conn, NCT)
    assert search["status"] == "unavailable" and search["attempts"] == 3 and search["answered_fully"] is False


def test_papers_are_fetched_in_batches(conn, settings):
    fake = FakePubMed(["1", "2", "3", "4", "5"])
    result, _ = run(conn, settings, fake)
    assert fake.fetches == 3 and result.papers_new == 5                 # batch size is 2: 2 + 2 + 1
    assert get_latest_paper_search(conn, NCT)["batches_total"] == 3


def test_a_failed_batch_is_counted_and_the_others_are_kept(conn, settings):
    fake = FakePubMed(["1", "2", "3", "4", "5"], overrides={2: httpx.Response(404)})
    result, _ = run(conn, settings, fake)
    assert result.status == "partial" and result.batches_failed == 1 and result.detail == "http_404"
    assert fake.fetches == 3                                            # 404 is permanent: not retried
    assert sorted(r["pmid"] for r in get_papers_for_trial(conn, NCT)) == ["1", "2", "5"]
    search = get_latest_paper_search(conn, NCT)
    assert search["status"] == "ok" and search["batches_failed"] == 1
    assert search["answered_fully"] is False                            # "ok" alone does not mean complete


def test_unreadable_xml_keeps_the_raw_file_and_counts_a_failed_batch(conn, settings):
    fake = FakePubMed(["111"], overrides={1: httpx.Response(200, content=b"<PubmedArticleSet><oops>")})
    result, _ = run(conn, settings, fake)
    assert result.status == "fetch_failed" and result.detail == "ParseError"     # the only batch failed
    assert len(xml_files(settings)) == 1 and get_papers_for_trial(conn, NCT) == []


def test_every_batch_failing_is_fetch_failed_not_partial(conn, settings):
    fake = FakePubMed(["1", "2", "3"], overrides={1: httpx.Response(404), 2: httpx.Response(404)})
    result, _ = run(conn, settings, fake)
    assert result.status == "fetch_failed" and result.batches_failed == 2 and result.papers_new == 0
    assert get_latest_paper_search(conn, NCT)["answered_fully"] is False


def test_papers_missing_from_a_successful_answer_are_counted(conn, settings):
    only_one = httpx.Response(200, content=f"<PubmedArticleSet>{article('1')}</PubmedArticleSet>".encode())
    result, _ = run(conn, settings, FakePubMed(["1", "2"], overrides={1: only_one}))
    assert result.status == "fetch_failed" and result.detail == "missing_papers"   # the only batch came back incomplete
    assert [r["pmid"] for r in get_papers_for_trial(conn, NCT)] == ["1"]            # what did arrive is kept
    assert get_latest_paper_search(conn, NCT)["batches_failed"] == 1


def test_too_many_requests_is_retried(conn, settings):
    fake = FakePubMed(["111"], overrides={1: httpx.Response(429)})
    result, waits = run(conn, settings, fake)
    assert result.status == "done" and fake.fetches == 2 and 1 in waits


def test_a_paper_that_does_not_list_the_trial_is_stored_as_not_confirmed(conn, settings):
    run(conn, settings, FakePubMed(["111", "222"], unlisted=["222"]))
    status = {r["pmid"]: r["status"] for r in get_papers_for_trial(conn, NCT)}
    assert status == {"111": "listed", "222": "not_confirmed"}


def test_it_pauses_before_every_request(conn, settings):
    fake = FakePubMed(["1", "2", "3"])
    _, waits = run(conn, settings, fake)
    assert waits == [PAUSE_SECONDS] * fake.requests


def test_running_twice_adds_no_papers_but_records_both_searches(conn, settings):
    run(conn, settings, FakePubMed(["111", "222"]))
    second, _ = run(conn, settings, FakePubMed(["111", "222"]))
    assert second.papers_new == 0 and len(get_papers_for_trial(conn, NCT)) == 2
    count = conn.execute("SELECT COUNT(*) FROM paper_searches WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert count == 2
