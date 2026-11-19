##############################################################################
# tests/test_abstract_baseline.py
#
# PURPOSE: Prove the code-only baseline: names are pulled out of long registered titles,
#          matched as whole words (dash = space, abbreviation = full name), every quote is
#          really in the abstract, role cues are read from every mention, conflicts are
#          "ambiguous", and a paper with no abstract is "no_text", never "not identified".
#          Small made-up abstracts, no database.
##############################################################################

import pytest

from trialtrace.compare.abstract_baseline import (
    AbstractMatch, TextQuote, candidate_names, find_name, match_one, match_outcomes_in_abstract,
    quote_is_in_text, sentence_spans,
)
from trialtrace.contracts import RegisteredOutcome
from trialtrace.parse.pubmed_parser import Paper

NCT = "NCT04644237"
HASH = "a" * 64


def reg(title, position=0, role="primary"):
    return RegisteredOutcome(nct_id=NCT, content_hash=HASH, role=role, position=position, measure_text=title)


def paper(abstract):
    return Paper(pmid="111", title="T", abstract=abstract, listed_nct_ids=[NCT])


def match(title, abstract, role="primary"):
    return match_one(reg(title, role=role), paper(abstract))


# --- names from titles --------------------------------------------------------

def test_a_bracket_abbreviation_and_the_main_phrase_are_both_names():
    names = candidate_names("Relapse-free Survival (RFS)")
    assert "rfs" in names and "relapse free survival" in names


def test_a_long_registered_title_is_cut_down_to_its_main_phrase():
    title = ("Percentage of Participants With Confirmed Objective Response Rate by Blinded Independent "
             "Central Review Following Intravenous Administration of Trastuzumab Deruxtecan in Participants")
    assert "confirmed objective response rate" in candidate_names(title)


def test_abbreviations_and_full_names_are_twins():
    assert {"os", "overall survival"} <= set(candidate_names("Overall Survival Following Treatment in Patients"))
    assert {"os", "overall survival"} <= set(candidate_names("Overall Survival (OS)"))


def test_names_have_no_repeats_and_no_empty_entries():
    names = candidate_names("Treatment Emergent Adverse Events (TEAE)")
    assert len(names) == len(set(names)) and "" not in names


# --- finding a name -----------------------------------------------------------

@pytest.mark.parametrize("name, text", [
    ("overall survival", "Overall Survival was longer."),
    ("progression free survival", "The progression-free survival was 5 months."),
    ("progression free survival", "The progression free   survival was 5 months."),
    ("os", "Median OS was 12 months."),
])
def test_a_name_is_found_as_whole_words(name, text):
    assert find_name(name, text) is not None


@pytest.mark.parametrize("name, text", [
    ("os", "The most common events were mild."),            # inside a word
    ("os", "The dose was 5 mg."),
    ("pfs", "PFSX was a different scale."),                 # a longer token
    ("overall survival", "Survival overall was better."),   # words in another order
])
def test_a_name_inside_another_word_or_in_another_order_is_not_found(name, text):
    assert find_name(name, text) is None


def test_an_empty_name_finds_nothing():
    assert find_name("", "Any text at all.") is None


# --- sentences and quotes -----------------------------------------------------

def test_sentence_offsets_cut_the_text_exactly():
    text = "First one. Second one? Third one."
    assert [text[s:e] for s, e in sentence_spans(text)] == ["First one.", "Second one?", "Third one."]


def test_a_dot_inside_a_version_number_does_not_end_a_sentence():
    text = "Response per RECIST v1.1 was assessed. Next."
    assert [text[s:e] for s, e in sentence_spans(text)][0] == "Response per RECIST v1.1 was assessed."


def test_an_identified_outcome_comes_with_its_exact_sentence():
    abstract = "We enrolled 50 patients. The primary end point was overall survival (OS). Safety was also assessed."
    found = match("Overall Survival (OS)", abstract)
    assert found.status == "identified" and found.quote.text == "The primary end point was overall survival (OS)."
    assert quote_is_in_text(found.quote, paper(abstract))


def test_a_quote_that_is_not_where_it_says_is_refused():
    abstract = "The primary end point was overall survival."
    assert not quote_is_in_text(TextQuote(text="invented sentence", start=0, end=17), paper(abstract))
    assert not quote_is_in_text(TextQuote(text="x", start=0, end=1), paper(None))


def test_the_abbreviation_in_the_abstract_matches_the_full_registered_name():
    assert match("Overall Survival Following Treatment", "Median OS was 12 months.").status == "identified"


def test_the_full_name_in_the_abstract_matches_the_registered_abbreviation():
    assert match("Progression-free Survival (PFS)", "Progression-free survival was 5 months.").status == "identified"


def test_dashes_and_spaces_are_interchangeable():
    assert match("Progression Free Survival", "The progression-free survival was 5 months.").status == "identified"


def test_the_long_registered_title_is_found_through_its_main_phrase():
    title = "Percentage of Participants With Confirmed Objective Response Rate by Blinded Independent Central Review"
    found = match(title, "The primary end point was confirmed objective response rate (ORR) per RECIST v1.1.")
    assert found.status == "identified" and found.reported_role == "primary"


# --- not found, and no text ---------------------------------------------------

def test_an_outcome_that_is_not_named_is_not_identified():
    found = match("Overall Survival (OS)", "We measured tumour size and quality of life.")
    assert found.status == "not_identified" and found.quote is None and found.matched_name is None


def test_a_reworded_outcome_is_missed_which_is_the_known_weakness_of_the_baseline():
    assert match("Overall Survival", "Time to death from any cause was longer.").status == "not_identified"


def test_the_letters_os_inside_other_words_do_not_count():
    assert match("Overall Survival (OS)", "The most common events were mild and the dose was low.").status == "not_identified"


@pytest.mark.parametrize("abstract", [None, "", "   "])
def test_no_abstract_is_no_text_not_not_identified(abstract):
    found = match("Overall Survival (OS)", abstract)
    assert found.status == "no_text" and found.quote is None


# --- role cues ----------------------------------------------------------------

def test_a_primary_sentence_gives_the_primary_cue():
    assert match("Overall Survival", "The primary end point was overall survival.").reported_role == "primary"


def test_a_secondary_sentence_gives_the_secondary_cue():
    found = match("Overall Survival", "Secondary end points included overall survival.", role="secondary")
    assert found.reported_role == "secondary"


def test_a_sentence_with_no_cue_gives_none():
    found = match("Overall Survival", "Overall survival was 12 months.")
    assert found.status == "identified" and found.reported_role is None


def test_a_cue_must_be_a_whole_word():
    assert match("Overall Survival", "Overall survival was measured in the primarycare setting.").reported_role is None


def test_conflicting_cues_across_mentions_are_ambiguous():
    abstract = ("The primary end point was relapse-free survival (RFS). "
                "Secondary end points included overall survival and the effect of MRD on RFS.")
    found = match("Relapse-free Survival (RFS)", abstract)
    assert found.status == "ambiguous" and found.quote is not None and found.reported_role is None


def test_one_sentence_naming_both_roles_is_ambiguous():
    found = match("Overall Survival", "Primary and secondary end points included overall survival.")
    assert found.status == "ambiguous"


def test_repeated_mentions_with_the_same_cue_stay_identified():
    abstract = "The primary end point was overall survival. The primary analysis of overall survival used a log-rank test."
    found = match("Overall Survival", abstract)
    assert found.status == "identified" and found.reported_role == "primary"


# --- the whole list -----------------------------------------------------------

def test_one_match_per_registered_outcome_in_the_registered_order():
    outcomes = [reg("Overall Survival (OS)", 0), reg("Quality of Life", 1, "secondary"), reg("Progression-free Survival (PFS)", 2, "secondary")]
    matches = match_outcomes_in_abstract(outcomes, paper("The primary end point was OS. PFS was secondary."))
    assert [(m.position, m.status) for m in matches] == [(0, "identified"), (1, "not_identified"), (2, "identified")]
    assert all(m.pmid == "111" and m.nct_id == NCT for m in matches)


def test_every_quote_in_a_list_is_really_in_the_abstract():
    abstract = "The primary end point was OS. PFS was secondary. Safety was assessed."
    outcomes = [reg("Overall Survival (OS)", 0), reg("Progression-free Survival (PFS)", 1, "secondary")]
    for m in match_outcomes_in_abstract(outcomes, paper(abstract)):
        assert m.quote is not None and quote_is_in_text(m.quote, paper(abstract))


def test_a_found_outcome_without_its_quote_is_refused():
    with pytest.raises(ValueError):
        AbstractMatch(nct_id=NCT, pmid="1", role="primary", position=0, measure_text="OS", status="identified")
