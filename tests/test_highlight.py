##############################################################################
# tests/test_highlight.py
#
# PURPOSE: Prove the quote is placed in the abstract only where it really is, never highlights the wrong text,
#          shows abstract text literally, and the review filters keep the right rows.
##############################################################################

from tests.test_match_review import NCT, add_finding
from trialtrace.review.highlight import escape_markdown, highlighted_markdown, locate_quote
from trialtrace.review.match_review import filter_rows, load_queue

ABSTRACT = "Patients were enrolled. Mortality fell by 20% (p=0.01). Safety was good."
QUOTE = "Mortality fell by 20% (p=0.01)."


def test_the_stored_position_is_used_when_it_holds_the_quote():
    start = ABSTRACT.index(QUOTE)
    assert locate_quote(ABSTRACT, QUOTE, start, start + len(QUOTE)) == ("Patients were enrolled. ", QUOTE, " Safety was good.")


def test_a_wrong_position_falls_back_to_finding_the_quote_in_the_text():
    assert locate_quote(ABSTRACT, QUOTE, 0, 5)[1] == QUOTE and locate_quote(ABSTRACT, QUOTE, None, None)[0] == "Patients were enrolled. "


def test_a_quote_that_is_not_in_the_abstract_is_not_highlighted():
    assert locate_quote(ABSTRACT, "Survival doubled.", 0, 17) is None
    assert highlighted_markdown(ABSTRACT, "Survival doubled.", 0, 17) is None
    assert highlighted_markdown(None, QUOTE, 0, 5) is None and highlighted_markdown(ABSTRACT, None, None, None) is None


def test_abstract_text_is_shown_literally_not_as_markdown_or_maths():
    assert escape_markdown("a*b_c [x] $5 #1") == "a\*b\_c \[x\] \$5 \#1"
    text = highlighted_markdown("Cost $5 and *bold* result. Key finding here.", "Key finding here.", None, None)
    assert text.startswith("Cost \$5 and \*bold\* result. :orange-background[Key finding here.]")


def row(status="identified", role="primary", nct="NCT00000001"):
    return {"status": status, "outcome_role": role, "nct_id": nct}


def test_filters_keep_the_right_rows_and_an_empty_choice_keeps_all():
    rows = [row("identified", "primary", "NCT00000001"), row("ambiguous", "secondary", "NCT00000002"), row("not_identified", "other", "NCT00000003")]
    assert len(filter_rows(rows)) == 3
    assert [r["status"] for r in filter_rows(rows, statuses=["ambiguous", "identified"])] == ["identified", "ambiguous"]
    assert [r["outcome_role"] for r in filter_rows(rows, roles=["other"])] == ["other"]
    assert [r["nct_id"] for r in filter_rows(rows, nct_text=" nct00000002 ")] == ["NCT00000002"]
    assert filter_rows(rows, statuses=["identified"], roles=["secondary"]) == []


def test_the_queue_carries_the_quote_position(conn):
    add_finding(conn, quote="mortality fell")
    queue_row = load_queue(conn, NCT)[0]
    assert (queue_row["quote_start"], queue_row["quote_end"]) == (0, len("mortality fell"))


def test_sentence_numbering_is_the_same_as_the_blind_sheets():
    from evals.prepare_v3 import spans
    from trialtrace.review.highlight import numbered_markdown, sentence_spans
    texts = ["One vs. two. Three! Four? e.g. five. Dr. Who said so.", "Single sentence only", "A. B. C.", "Mean 5.2 (SD 1.1) vs. 4.9. p = 0.01."]
    for text in texts:
        assert sentence_spans(text) == spans(text)
    assert numbered_markdown("First. Second.").startswith("**[1]** First.") and "**[2]** Second." in numbered_markdown("First. Second.")
