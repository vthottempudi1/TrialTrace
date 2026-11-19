##############################################################################
# tests/test_safety_baseline.py
#
# PURPOSE: Prove the code-only safety reader (step S5): it finds death, serious-event and named-event
#          sentences, takes the numbers written with them, reads "no deaths" as negated, keeps
#          treatment-related apart, never reads ratios, grades, time points or doses as counts,
#          every quote is really in the abstract, and no abstract is "no_text", never "nothing stated".
#          Small made-up abstracts, no database.
##############################################################################

from trialtrace.compare.abstract_baseline import quote_is_in_text
from trialtrace.safety.safety_baseline import clause_bounds, is_negated, read_numbers, read_safety_statements
from trialtrace.parse.pubmed_parser import Paper


def paper(abstract):
    return Paper(pmid="111", title="T", abstract=abstract)


def read(abstract):
    return read_safety_statements(paper(abstract)).statements


def one(abstract, kind, term=None):
    """The single statement of this kind (and term) in the abstract."""
    found = [s for s in read(abstract) if s.kind == kind and s.term == term]
    assert len(found) == 1
    return found[0]


# --- deaths -------------------------------------------------------------------

def test_a_death_sentence_is_found_with_its_numbers():
    statement = one("Efficacy was good. There were 6 deaths in the drug group and 4 in the placebo group.", "death")
    assert statement.numbers == ["6", "4"] and statement.sentence == 2 and not statement.negated


def test_died_mortality_and_fatal_are_death_words():
    for text in ("Three patients died.", "Mortality was 12% versus 15%.", "Two fatal events occurred."):
        assert [s.kind for s in read(text)] == ["death"]


def test_no_deaths_is_negated_not_dropped():
    statement = one("No deaths occurred during the study.", "death")
    assert statement.negated and statement.numbers == []


def test_none_of_the_patients_died_is_negated():
    assert one("None of the patients died.", "death").negated


def test_no_difference_in_mortality_is_not_no_deaths():
    assert not one("There was no difference in mortality (12% vs 13%).", "death").negated


def test_treatment_related_deaths_are_marked():
    statement = one("There were 2 treatment-related deaths.", "death")
    assert statement.treatment_related and statement.numbers == ["2"]
    assert not one("There were 2 deaths.", "death").treatment_related


# --- serious adverse events ----------------------------------------------------

def test_serious_adverse_events_are_found_with_counts_and_percents():
    statement = one("Serious adverse events occurred in 42 (41.6%) of 101 patients and 26 (52.0%) of 50 patients.", "serious")
    assert statement.numbers == ["42", "41.6%", "101", "26", "52.0%", "50"]


def test_sae_and_serious_teae_spellings_are_found():
    for text in ("SAEs were reported in 5% of patients.", "Serious treatment-emergent adverse events occurred in 5%.",
                 "Serious AEs occurred in 5%."):
        assert [s.kind for s in read(text)] == ["serious"]


def test_no_serious_adverse_events_is_negated():
    statements = read("No deaths or serious adverse events were reported.")
    assert [(s.kind, s.negated) for s in statements] == [("death", True), ("serious", True)]


def test_plain_adverse_events_are_not_serious():
    assert read("Adverse events occurred in 80% of patients.") == []


# --- named events --------------------------------------------------------------

def test_each_named_event_gets_the_number_in_its_own_clause():
    statements = read("The most common adverse events were neutropenia (21%), anemia (10%), and nausea (5%).")
    assert [(s.term, s.numbers) for s in statements] == [("neutropenia", ["21%"]), ("anaemia", ["10%"]), ("nausea", ["5%"])]


def test_spelling_variants_give_one_name():
    assert one("Diarrhea occurred in 12 patients.", "event", "diarrhoea").numbers == ["12"]
    assert one("Neutrophil count decreased in 30%.", "event", "neutropenia").numbers == ["30%"]


def test_febrile_neutropenia_is_not_also_neutropenia():
    assert [s.term for s in read("Febrile neutropenia occurred in 5%.")] == ["febrile neutropenia"]
    both = read("Neutropenia (20%) and febrile neutropenia (5%) were reported.")
    assert sorted(s.term for s in both) == ["febrile neutropenia", "neutropenia"]


def test_a_plural_is_found_but_a_longer_word_is_not():
    assert [s.term for s in read("Infections were seen in 9%.")] == ["infection"]
    assert read("Rashford scored in 9% of games.") == []


def test_the_grade_is_kept_and_is_not_a_number():
    statement = one("Grade 3 or higher neutropenia occurred in 21% of patients.", "event", "neutropenia")
    assert statement.grade == "Grade 3 or higher" and statement.numbers == ["21%"]
    assert one("Nausea occurred in 21%.", "event", "nausea").grade is None


def test_an_event_named_without_a_number_has_no_numbers():
    statement = one("The most common events were nausea, fatigue and headache.", "event", "nausea")
    assert statement.numbers == []


# --- numbers -------------------------------------------------------------------

def test_ratios_p_values_and_confidence_intervals_are_not_counts():
    assert read_numbers("12% vs 15% (HR 0.80; 95% CI 0.61-1.05; p=0.04)") == ["12%", "15%"]


def test_time_points_doses_and_phases_are_not_counts():
    assert read_numbers("at 12 weeks with 10 mg in this phase 3 trial, on day 28, 5.4 mg/kg, 7 patients") == ["7"]


def test_a_clause_ends_at_a_comma_outside_brackets():
    sentence = "nausea (20%, mostly mild), fatigue (5%)"
    left, right = clause_bounds(sentence, 0, 6)
    assert sentence[left:right] == "nausea (20%, mostly mild)"


def test_negation_looks_only_at_the_words_just_before():
    assert is_negated("There were no") and is_negated("without any")
    assert not is_negated("No patients in group A but several in group B had")


# --- quotes and unknown ----------------------------------------------------------

def test_every_quote_is_the_exact_sentence_in_the_abstract():
    abstract = "BACKGROUND: text.\nRESULTS: Six patients died. Nausea occurred in 5%.\nCONCLUSION: fine."
    statements = read(abstract)
    assert [s.sentence for s in statements] == [2, 3]
    assert statements[0].quote.text == "RESULTS: Six patients died."
    assert all(quote_is_in_text(s.quote, paper(abstract)) for s in statements)


def test_no_abstract_is_no_text_not_nothing_stated():
    assert read_safety_statements(paper(None)).status == "no_text"
    assert read_safety_statements(paper("   ")).status == "no_text"
    nothing = read_safety_statements(paper("The drug improved survival."))
    assert nothing.status == "ok" and nothing.statements == []


# --- found on the real abstracts -------------------------------------------------

def test_programmed_cell_death_ligand_is_a_protein_not_a_death():
    assert read("Patients with programmed cell death-ligand 1 score above 1 responded.") == []
    assert read("Programmed death-ligand 1 expression was high.") == []
    assert [s.kind for s in read("Cardiovascular death occurred in 5%.")] == ["death"]


def test_a_middle_dot_decimal_is_one_number():
    assert read_numbers("103 (5·4%) of 1914, p=0·0045") == ["103", "5.4%", "1914"]


def test_stage_numbers_are_not_counts():
    assert read_numbers("death in 52 people with CKD stages 1-2") == ["52"]


def test_vs_with_a_dot_does_not_end_the_sentence():
    statement = one("Infection occurred in 19.9% vs. 12.1% of patients. Next sentence.", "event", "infection")
    assert statement.numbers == ["19.9%", "12.1%"]
    assert statement.quote.text == "Infection occurred in 19.9% vs. 12.1% of patients."
