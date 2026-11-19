##############################################################################
# tests/test_link_review.py
#
# PURPOSE: Prove the paper-relationship answers follow the rules (reviewer required, "not this trial" needs
#          a reason, bad words refused), are kept as history, and the question loop works.
#          Uses the test database; every test is rolled back.
##############################################################################

from datetime import datetime, timezone

import psycopg
import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.db.repository import get_link_reviews, save_papers
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.review.link_review import record_link_review, review_links

NCT = "NCT04644237"
HASH = "a" * 64


@pytest.fixture
def two_papers(conn):
    papers = [Paper(pmid=pmid, title=f"Paper {pmid}", listed_nct_ids=[NCT]) for pmid in ("111", "222")]
    links = [check_paper_link(p, NCT) for p in papers]
    save_papers(conn, papers, links, HASH, "data/raw/a.xml", "https://example.org/efetch", datetime(2026, 11, 3, tzinfo=timezone.utc))


def answers(conn):
    return {pmid: row["relationship"] for (pmid, _), row in get_link_reviews(conn, NCT).items()}


def scripted(*replies):
    """An ask function that returns the replies in order."""
    queue = list(replies)
    return lambda question: queue.pop(0)


def test_answer_is_saved_and_read_back(conn, two_papers):
    record_link_review(conn, NCT, "111", HASH, "reports_results", "Dr Rao", "Primary results paper")
    row = get_link_reviews(conn, NCT)[("111", HASH)]
    assert row["relationship"] == "reports_results" and row["reviewer"] == "Dr Rao" and row["reason"] == "Primary results paper"


def test_changing_your_mind_keeps_history_and_newest_is_current(conn, two_papers):
    record_link_review(conn, NCT, "111", HASH, "mentions_only", "Dr Rao")
    record_link_review(conn, NCT, "111", HASH, "reports_results", "Dr Rao")
    assert answers(conn) == {"111": "reports_results"}
    assert conn.execute("SELECT COUNT(*) FROM paper_link_reviews").fetchone()[0] == 2


def test_reviewer_is_required(conn, two_papers):
    with pytest.raises(ValueError, match="reviewer"):
        record_link_review(conn, NCT, "111", HASH, "reports_results", "  ")


def test_unknown_relationship_is_refused(conn, two_papers):
    with pytest.raises(ValueError, match="relationship"):
        record_link_review(conn, NCT, "111", HASH, "primary_paper", "Dr Rao")


def test_not_this_trial_needs_a_reason(conn, two_papers):
    with pytest.raises(ValueError, match="reason"):
        record_link_review(conn, NCT, "111", HASH, "not_this_trial", "Dr Rao")
    record_link_review(conn, NCT, "111", HASH, "not_this_trial", "Dr Rao", "Different drug")
    assert answers(conn) == {"111": "not_this_trial"}


def test_database_also_refuses_not_this_trial_without_reason(conn, two_papers):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO paper_link_reviews (nct_id, pmid, raw_hash, relationship, reviewer) VALUES (%s, '111', %s, 'not_this_trial', 'Dr Rao')",
            (NCT, HASH),
        )


def test_answer_for_a_paper_that_is_not_linked_is_refused(conn, two_papers):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record_link_review(conn, NCT, "999", HASH, "reports_results", "Dr Rao")


def test_loop_asks_only_about_unanswered_papers(conn, two_papers):
    record_link_review(conn, NCT, "111", HASH, "mentions_only", "Dr Rao")
    summary = review_links(conn, NCT, "Dr Rao", ask=scripted("r", ""), show=lambda text: None)
    assert answers(conn) == {"111": "mentions_only", "222": "reports_results"}
    assert summary["reports_results"] == 1


def test_loop_skip_and_quit_save_nothing_more(conn, two_papers):
    summary = review_links(conn, NCT, "Dr Rao", ask=scripted("s", "q"), show=lambda text: None)
    assert summary["skipped"] == 1
    assert answers(conn) == {}


def test_loop_keeps_asking_for_a_reason_when_not_this_trial(conn, two_papers):
    review_links(conn, NCT, "Dr Rao", ask=scripted("n", "", "Different drug", "q"), show=lambda text: None)
    assert answers(conn) == {"111": "not_this_trial"}


def test_loop_commits_after_each_answer(conn, two_papers):
    commits = []
    review_links(conn, NCT, "Dr Rao", ask=scripted("m", "", "p", ""), show=lambda text: None, commit=lambda: commits.append(1))
    assert len(commits) == 2
