##############################################################################
# trialtrace/safety/safety_baseline.py
#
# PURPOSE: Safety, step S5: the code-only baseline reader for a paper's abstract. No LLM.
#          It finds the sentences that state something about safety and the numbers written with them:
#            death    the sentence mentions death / died / mortality / fatal
#            serious  the sentence mentions serious adverse events (or SAE)
#            event    the sentence names a common adverse event from EVENT_TERMS (e.g. neutropenia)
#          It only READS: nothing is compared with the registry here (that is step S7).
#          This is the score the model reader (S6) must beat, so it stays simple on purpose.
#
# HOW NUMBERS ARE TAKEN:
#   - death and serious: every count or percent in the SENTENCE
#   - event: only the counts or percents in the event's own CLAUSE (up to the nearest comma or
#     semicolon outside brackets), because abstracts list many events in one sentence
#   - not counted as numbers: decimals without % (hazard ratios, p values), "95% CI", grades,
#     time points ("12 weeks", "day 28"), doses ("10 mg") and "phase 3"
#
# KNOWN WEAKNESSES (the model reader should do better):
#   - it cannot tell a death that is an efficacy result (mortality as the outcome) from a safety death
#   - "nausea, vomiting and diarrhoea (20%, 15% and 10%)" gives all three numbers to diarrhoea
#   - it does not know which treatment group a number belongs to
#   - only the events in EVENT_TERMS are found
#
# USE:     reading = read_safety_statements(paper)      # SafetyReading(status, statements)
##############################################################################

import re
from typing import Literal

from pydantic import BaseModel, Field

from trialtrace.compare.abstract_baseline import TextQuote, sentence_spans
from trialtrace.parse.pubmed_parser import Paper

# "programmed cell death-ligand 1" (PD-L1) is a protein name, not a death
DEATH = re.compile(
    r"\b(?<!programmed )(?<!cell )(?:deaths?|died|mortality|fatal|fatality|fatalities)\b(?![- ]ligand)(?![- ]1\b)",
    re.IGNORECASE,
)
SERIOUS = re.compile(
    r"\bserious\s+(?:treatment[- ]emergent\s+)?(?:adverse\s+(?:events?|reactions?|effects?)|AEs?|TEAEs?)\b|\bSAEs?\b",
    re.IGNORECASE,
)

# our name for the event -> the spellings searched for (a plural "s" is also accepted)
EVENT_TERMS = {
    "febrile neutropenia": ["febrile neutropenia"],
    "neutropenia": ["neutropenia", "neutrophil count decreased", "decreased neutrophil count"],
    "anaemia": ["anaemia", "anemia"],
    "thrombocytopenia": ["thrombocytopenia", "platelet count decreased", "decreased platelet count"],
    "leukopenia": ["leukopenia", "leucopenia"],
    "lymphopenia": ["lymphopenia"],
    "interstitial lung disease": ["interstitial lung disease"],
    "pneumonitis": ["pneumonitis"],
    "pneumonia": ["pneumonia"],
    "nausea": ["nausea"],
    "vomiting": ["vomiting"],
    "diarrhoea": ["diarrhoea", "diarrhea"],
    "constipation": ["constipation"],
    "decreased appetite": ["decreased appetite"],
    "stomatitis": ["stomatitis"],
    "fatigue": ["fatigue"],
    "headache": ["headache"],
    "dizziness": ["dizziness"],
    "rash": ["rash", "rashes"],
    "pruritus": ["pruritus"],
    "alopecia": ["alopecia"],
    "pyrexia": ["pyrexia", "fever"],
    "infection": ["infection"],
    "sepsis": ["sepsis"],
    "hypertension": ["hypertension"],
    "hypotension": ["hypotension"],
    "haemorrhage": ["haemorrhage", "hemorrhage", "bleeding"],
    "hypoglycaemia": ["hypoglycaemia", "hypoglycemia"],
    "hyperglycaemia": ["hyperglycaemia", "hyperglycemia"],
    "peripheral neuropathy": ["peripheral neuropathy"],
    "arthralgia": ["arthralgia"],
    "myalgia": ["myalgia"],
    "cough": ["cough"],
    "dyspnoea": ["dyspnoea", "dyspnea"],
    "insomnia": ["insomnia"],
    "injection site reaction": ["injection site reaction"],
}

GRADE = re.compile(
    r"\bgrades?\s*(?:≥|>=)?\s*\d(?:\s*(?:or|to|and|-|–|/)\s*(?:\d|higher|worse|greater|above))?", re.IGNORECASE)
RELATED = re.compile(r"\b(?:treatment|drug|study drug|therapy)[- ]related\b|\brelated to (?:the )?(?:study )?(?:treatment|drug)", re.IGNORECASE)
NEGATION_WORDS = {"no", "none", "zero", "without"}
NOT_AN_ABSENCE = {"difference", "differences", "differ", "increase", "increased", "reduction", "effect"}   # "no difference in deaths" is not "no deaths"

# Things with a digit that are not a count of people: removed before numbers are read
NOT_A_COUNT = [
    GRADE,
    re.compile(r"\d+(?:\.\d+)?\s?%\s*(?:CI|confidence)", re.IGNORECASE),                                  # 95% CI
    re.compile(r"\b(?:phase|week|day|month|year|cycle|visit|stages?|score)\s*=?\s*\d+(?:\s*-\s*\d+)?\b", re.IGNORECASE),   # week 12, stages 1-2
    re.compile(r"\d+(?:\.\d+)?(?:\s*(?:or|to|and|-|–)\s*\d+(?:\.\d+)?)?[\s-]*(?:months?|weeks?|days?|years?|hours?)\b", re.IGNORECASE),
    re.compile(r"\d+(?:\.\d+)?\s*(?:mg|kg|ml|mcg|μg|µg|g|mmol|iu|units?)\b(?:/\w+)?", re.IGNORECASE),       # doses
]
NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s?%?")


class SafetyStatement(BaseModel):
    """One thing an abstract says about safety, with the sentence it came from."""

    pmid: str
    kind: Literal["death", "serious", "event"]
    term: str | None = None                         # for kind "event": our name for it (EVENT_TERMS key)
    numbers: list[str] = Field(default_factory=list)    # as written, in order: "6", "21%"; empty = none stated
    grade: str | None = None                        # e.g. "grade 3 or higher", as written; None = not stated
    negated: bool = False                           # "no deaths", "no serious adverse events"
    treatment_related: bool = False                 # "treatment-related deaths": not the same as all deaths
    sentence: int = Field(ge=1)                     # 1 = the first sentence of the abstract
    quote: TextQuote                                # the whole sentence, with its place in the abstract


class SafetyReading(BaseModel):
    """What the baseline read from one paper. no_text = the paper has no abstract: we could not look (unknown)."""

    pmid: str
    status: Literal["ok", "no_text"]
    statements: list[SafetyStatement] = Field(default_factory=list)


def term_pattern(spelling: str) -> re.Pattern:
    """The spelling as whole words, any space or dash between words, with an optional plural s."""
    words = r"[\s-]+".join(re.escape(word) for word in spelling.split())
    return re.compile(rf"(?<![A-Za-z]){words}s?(?![A-Za-z])", re.IGNORECASE)


def clause_bounds(sentence: str, start: int, end: int) -> tuple[int, int]:
    """(start, end) of the clause holding sentence[start:end]: cut at the nearest comma or semicolon outside brackets."""
    cuts = []
    depth = 0
    for index, character in enumerate(sentence):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character in ",;" and depth == 0:
            cuts.append(index)
    left, right = 0, len(sentence)
    for cut in cuts:
        if cut < start:
            left = cut + 1
        elif cut >= end:
            right = cut
            break
    return left, right


def read_numbers(text: str) -> list[str]:
    """The counts and percents in the text, as written. Decimals without % (ratios, p values) are left out."""
    text = text.replace("·", ".")                   # some journals print 5·4% for 5.4%
    for pattern in NOT_A_COUNT:
        text = pattern.sub(" ", text)
    numbers = []
    for found in NUMBER.finditer(text):
        number = found.group(0).replace(" ", "")
        if number.endswith("%") or number.isdigit():
            numbers.append(number)
    return numbers


def is_negated(text_before: str) -> bool:
    """True if one of the four words just before the keyword says there were none ("no deaths")."""
    words = re.findall(r"[a-z]+", text_before.lower())[-4:]
    if any(word in NOT_AN_ABSENCE for word in words):
        return False
    return any(word in NEGATION_WORDS for word in words)


def read_sentence(pmid: str, sentence: str, number: int, quote: TextQuote) -> list[SafetyStatement]:
    """Read deaths and serious events first, then look for named events.

    Death and serious-event numbers come from the whole sentence.
    Named-event numbers come only from the clause containing that event.
    """
    details = {"pmid": pmid, "sentence": number, "quote": quote}
    statements = read_deaths_and_serious_events(sentence, details)
    statements.extend(read_named_events(sentence, details))
    return statements


def read_deaths_and_serious_events(sentence: str, details: dict) -> list[SafetyStatement]:
    """Find at most one death statement and one serious-event statement."""
    statements = []
    for kind, pattern in (("death", DEATH), ("serious", SERIOUS)):
        found = pattern.search(sentence)
        if found is None:
            continue
        left, right = clause_bounds(sentence, found.start(), found.end())
        clause = sentence[left:right]
        grade = GRADE.search(sentence)
        statements.append(SafetyStatement(
            **details, kind=kind, numbers=read_numbers(sentence),
            grade=grade.group(0) if grade else None,
            negated=is_negated(sentence[left:found.start()]),
            treatment_related=RELATED.search(clause) is not None,
        ))
    return statements


def read_named_events(sentence: str, details: dict) -> list[SafetyStatement]:
    """Find one statement per known event, using numbers from its own clause.

    Blank out each matched name so overlapping names are not counted twice:
    "febrile neutropenia" should not also produce "neutropenia".
    Spaces preserve positions in the original sentence.
    """
    statements = []
    remaining = sentence
    for term, spellings in EVENT_TERMS.items():
        for spelling in spellings:
            found = term_pattern(spelling).search(remaining)
            if found is None:
                continue
            left, right = clause_bounds(sentence, found.start(), found.end())
            clause = sentence[left:right]
            grade = GRADE.search(clause)
            statements.append(SafetyStatement(
                **details, kind="event", term=term, numbers=read_numbers(clause),
                grade=grade.group(0) if grade else None,
                negated=is_negated(sentence[left:found.start()]),
                treatment_related=RELATED.search(clause) is not None,
            ))
            remaining = remaining[:found.start()] + " " * (found.end() - found.start()) + remaining[found.end():]
            break                                   # one statement per event per sentence
    return statements


def safety_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentences as (start, end). A piece ending in "vs." is joined to the next: "12% vs. 15%" is one sentence."""
    spans = []
    for start, end in sentence_spans(text):
        if spans and text[spans[-1][0]:spans[-1][1]].lower().endswith(("vs.", "e.g.", "i.e.")):
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans


def read_safety_statements(paper: Paper) -> SafetyReading:
    """Read the abstract sentence by sentence, without calling an AI model.

    1. Return no_text if the abstract is missing.
    2. Split the abstract into sentences, keeping their original positions.
    3. Collect death, serious-event and named-event statements from each sentence.
    """
    if not paper.abstract or not paper.abstract.strip():
        return SafetyReading(pmid=paper.pmid, status="no_text")         # we could not look: unknown, not "nothing stated"

    abstract = paper.abstract
    statements = []
    for index, (start, end) in enumerate(safety_sentence_spans(abstract)):
        quote = TextQuote(text=abstract[start:end], start=start, end=end)
        sentence_number = index + 1
        statements.extend(read_sentence(paper.pmid, quote.text, sentence_number, quote))
    return SafetyReading(pmid=paper.pmid, status="ok", statements=statements)
