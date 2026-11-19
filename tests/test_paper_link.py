##############################################################################
# tests/test_paper_link.py
#
# PURPOSE: Check how a paper is tied to a trial: listed, text_only, not_confirmed,
#          and the shared flag. No network, no database.
##############################################################################

from trialtrace.checks.paper_link import check_paper_link, mentions
from trialtrace.parse.pubmed_parser import Paper

NCT = "NCT04644237"


def paper(listed=(), title="A title", abstract=None):
    return Paper(pmid="111", title=title, abstract=abstract, listed_nct_ids=list(listed))


def test_listed_id_is_listed():
    link = check_paper_link(paper(listed=[NCT]), NCT)
    assert link.status == "listed" and not link.shared and link.other_nct_ids == []


def test_listed_id_in_lower_case_still_counts():
    assert check_paper_link(paper(listed=["nct04644237 "]), NCT).status == "listed"


def test_id_only_in_abstract_is_text_only():
    link = check_paper_link(paper(abstract=f"Registered as {NCT}."), NCT)
    assert link.status == "text_only"


def test_id_only_in_title_is_text_only():
    assert check_paper_link(paper(title=f"Trial {NCT}"), NCT).status == "text_only"


def test_listed_wins_over_text():
    link = check_paper_link(paper(listed=[NCT], abstract=f"{NCT}"), NCT)
    assert link.status == "listed"


def test_nothing_found_is_not_confirmed():
    assert check_paper_link(paper(), NCT).status == "not_confirmed"


def test_only_other_trials_listed_is_not_confirmed_but_shows_them():
    link = check_paper_link(paper(listed=["NCT00000001"]), NCT)
    assert link.status == "not_confirmed" and link.other_nct_ids == ["NCT00000001"]


def test_other_trials_alongside_ours_is_shared():
    link = check_paper_link(paper(listed=[NCT, "NCT00000002", "NCT00000001"]), NCT)
    assert link.status == "listed" and link.shared
    assert link.other_nct_ids == ["NCT00000001", "NCT00000002"]      # sorted, ours excluded


def test_longer_id_does_not_match():
    assert not mentions("NCT046442370 was a different trial", NCT)
    assert not mentions("NCT04644237a", NCT)


def test_id_inside_a_word_does_not_match():
    assert not mentions("xNCT04644237", NCT)


def test_id_with_punctuation_matches():
    assert mentions(f"(ClinicalTrials.gov: {NCT}).", NCT)


def test_missing_text_is_not_a_match():
    assert not mentions(None, NCT)
