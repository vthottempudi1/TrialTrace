##############################################################################
# tests/test_papers_repository.py
#
# PURPOSE: Check saving and reading papers and their trial links. Runs on the
#          test database; the shared `conn` fixture rolls every test back.
##############################################################################

from datetime import datetime, timezone

import psycopg
import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.db.repository import get_papers_for_trial, save_papers
from trialtrace.parse.pubmed_parser import Paper

NCT = "NCT04644237"
HASH_A = "a" * 64
HASH_B = "b" * 64
WHEN = datetime(2026, 11, 3, tzinfo=timezone.utc)


def paper(pmid="111", **fields):
    return Paper(pmid=pmid, title=f"Paper {pmid}", listed_nct_ids=[NCT], **fields)


def save(conn, papers, raw_hash=HASH_A, links=None, retrieved_at=WHEN):
    links = [check_paper_link(p, NCT) for p in papers] if links is None else links
    return save_papers(conn, papers, links, raw_hash, f"data/raw/{raw_hash[:8]}.xml", "https://example.org/efetch", retrieved_at)


def test_round_trip_keeps_paper_and_verdict(conn):
    save(conn, [paper("111", abstract="An abstract.", year="2020 Jan-Feb")])
    [row] = get_papers_for_trial(conn, NCT)
    assert row["pmid"] == "111" and row["raw_hash"] == HASH_A
    assert row["abstract"] == "An abstract." and row["year"] == "2020 Jan-Feb"
    assert row["status"] == "listed" and row["shared"] is False and row["reason"]


def test_missing_parts_stay_null(conn):
    save(conn, [paper("111")])
    [row] = get_papers_for_trial(conn, NCT)
    assert row["abstract"] is None and row["journal"] is None and row["year"] is None


def test_same_file_saved_twice_adds_nothing(conn):
    assert save(conn, [paper("111"), paper("222")]) == 2
    assert save(conn, [paper("111"), paper("222")]) == 0
    assert len(get_papers_for_trial(conn, NCT)) == 2


def test_a_newer_file_gives_a_second_row(conn):
    save(conn, [paper("111")], raw_hash=HASH_A)
    save(conn, [paper("111")], raw_hash=HASH_B, retrieved_at=datetime(2026, 11, 4, tzinfo=timezone.utc))
    rows = get_papers_for_trial(conn, NCT)
    assert [r["raw_hash"] for r in rows] == [HASH_B, HASH_A]          # newest download first


def test_link_without_its_paper_is_refused(conn):
    stray = check_paper_link(paper("999"), NCT)
    with pytest.raises(ValueError):
        save(conn, [paper("111")], links=[stray])
    assert get_papers_for_trial(conn, NCT) == []                      # nothing was saved


def test_database_itself_refuses_a_link_without_a_paper(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO paper_links (nct_id, pmid, raw_hash, status, shared, reason) "
            "VALUES (%s, '999', %s, 'listed', false, 'x')", (NCT, HASH_A))


def test_nothing_stored_gives_an_empty_list(conn):
    assert get_papers_for_trial(conn, NCT) == []


def test_other_trials_papers_are_not_returned(conn):
    save(conn, [paper("111")])
    assert get_papers_for_trial(conn, "NCT00000001") == []


def test_shared_paper_keeps_other_trial_ids(conn):
    shared = Paper(pmid="111", title="T", listed_nct_ids=[NCT, "NCT00000002"])
    save(conn, [shared])
    [row] = get_papers_for_trial(conn, NCT)
    assert row["shared"] is True and row["other_nct_ids"] == ["NCT00000002"]
