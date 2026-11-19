##############################################################################
# tests/test_sponsor.py
#
# PURPOSE: Prove name variants share one key, different sponsors are never
#          merged, and a missing name stays unknown.
##############################################################################

import pytest

from trialtrace.parse.sponsor import normalize_sponsor


@pytest.mark.parametrize("variant", [
    "Acme Pharma, Inc.", "ACME PHARMA INC", "acme pharma", "  Acme   Pharma  Inc  ", "Acme Pharma Inc.",
])
def test_variants_share_one_key(variant):
    assert normalize_sponsor(variant) == "acme pharma"


def test_hyphen_becomes_a_space_not_a_merge():
    assert normalize_sponsor("Bristol-Myers Squibb Company") == "bristol myers squibb"


def test_stacked_legal_endings_are_all_dropped():
    assert normalize_sponsor("Acme Pharma Co., Ltd.") == "acme pharma"


def test_legal_word_in_the_middle_is_kept():
    assert normalize_sponsor("Company Health Foundation") == "company health foundation"


def test_different_sponsors_are_not_merged():
    assert normalize_sponsor("Pfizer") != normalize_sponsor("Pfizer Pharmaceuticals")


def test_non_english_names_are_not_erased_and_accents_are_ignored():
    assert normalize_sponsor("Universität Zürich") == "universitat zurich"
    assert normalize_sponsor("Universität Zürich") == normalize_sponsor("Universitat Zurich")


def test_turkish_dotted_capital_i_is_not_split_into_pieces():
    # Found on real data: "İSMAİL CEYLAN" used to become "i smai l ceylan"
    assert normalize_sponsor("İSMAİL CEYLAN") == "ismail ceylan"


def test_a_leftover_and_after_a_legal_ending_is_dropped():
    # Found on real data: "Eli Lilly and Company" used to become "eli lilly and"
    assert normalize_sponsor("Eli Lilly and Company") == normalize_sponsor("Eli Lilly") == "eli lilly"


@pytest.mark.parametrize("written, expected", [
    ("mAbxience Research S.L.", "mabxience research"),      # found on real data
    ("Acme Pharma S.A.", "acme pharma"),
    ("Acme Pharma L.L.C.", "acme pharma"),
    ("Acme Pharma B.V.", "acme pharma"),
    ("Acme Pharma S.p.A.", "acme pharma"),
])
def test_legal_endings_written_with_dots_are_dropped(written, expected):
    assert normalize_sponsor(written) == expected


def test_a_dot_inside_a_name_does_not_split_it():
    assert normalize_sponsor("St. Jude Children's Research Hospital") == "st jude children s research hospital"


def test_and_inside_a_name_is_kept():
    assert normalize_sponsor("Smith and Nephew plc") == "smith and nephew"


@pytest.mark.parametrize("name", [None, "", "   ", "Inc.", "..."])
def test_missing_or_empty_name_is_unknown(name):
    assert normalize_sponsor(name) is None
