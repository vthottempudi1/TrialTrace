##############################################################################
# trialtrace/compare/abstract_baseline.py
#
# PURPOSE: The code-only baseline for question 5b, paper arm. For each REGISTERED outcome,
#          is it identified in a paper's abstract? No LLM. This is the score the LLM
#          version must beat later (rule 7), so it stays simple on purpose.
#          Per outcome it answers:
#            identified      a name for the outcome is in the abstract (with the exact sentence)
#            not_identified  no name found "in the available text (abstract)", never "not in the paper"
#            ambiguous       found, but the abstract's role cues conflict (primary AND secondary)
#            no_text         the paper has no abstract: we could not look (unknown)
#          It also reads the role cue ("primary end point", "secondary") from the sentence.
#          Use it only on papers whose link check says `listed`.
#          Known weakness: it matches names, not meaning. A reworded outcome is "not_identified".
#
# HOW A NAME IS FOUND:
#   1. candidate_names: from the registered title, the text in brackets ("RFS") and the main
#      phrase ("Percentage of Participants With Confirmed Objective Response Rate by ..." becomes
#      "confirmed objective response rate"); each also tried with its abbreviation or full name.
#   2. find_name: whole-word search in the original abstract; dashes and spaces are interchangeable.
#   3. the sentence around the hit is the quote; its offsets prove the quote is really in the text.
#
# USE:     matches = match_outcomes_in_abstract(registered_outcomes, paper)
##############################################################################

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from trialtrace.contracts import RegisteredOutcome
from trialtrace.parse.pubmed_parser import Paper

ABBREVIATIONS = {                                   # short name -> full name; both are searched
    "os": "overall survival",
    "pfs": "progression free survival",
    "rfs": "relapse free survival",
    "efs": "event free survival",
    "dfs": "disease free survival",
    "orr": "objective response rate",
    "dor": "duration of response",
    "dcr": "disease control rate",
    "teae": "treatment emergent adverse events",
    "ae": "adverse events",
}
LEADING_WORDS = re.compile(r"^(percentage|number|proportion|mean|median) of (participants|patients|subjects)( with| who)?\s+")
CUT_BEFORE = (" following ", " in participants", " in patients", " by ", " as assessed", " per ", " at ")   # where the main phrase ends


class TextQuote(BaseModel):
    """A piece of the abstract, with where it sits, so code can check it is really there."""

    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class AbstractMatch(BaseModel):
    """What the baseline found for ONE registered outcome in ONE paper's abstract."""

    nct_id: str
    pmid: str
    role: str                                       # the registered role
    position: int                                   # the registered outcome's position
    measure_text: str
    status: Literal["identified", "not_identified", "ambiguous", "no_text"]
    matched_name: str | None = None                 # which name was found
    quote: TextQuote | None = None                  # the first sentence containing it
    reported_role: Literal["primary", "secondary"] | None = None    # None = the abstract does not say clearly

    @model_validator(mode="after")
    def found_means_quote(self):
        if self.status in ("identified", "ambiguous") and self.quote is None:
            raise ValueError("a found outcome needs its quote")
        return self


def clean_words(text: str) -> str:
    """Lowercase words only: dashes and punctuation become spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def candidate_names(measure_text: str) -> list[str]:
    """Names to look for, most specific first: bracket text, then the main phrase, plus abbreviation twins."""
    names = [clean_words(inside) for inside in re.findall(r"\(([^)]{2,25})\)", measure_text)]

    main = re.sub(r"\([^)]*\)", " ", measure_text)
    main = LEADING_WORDS.sub("", main.strip().lower() + " ")
    main = " " + main.strip() + " "
    for word in CUT_BEFORE:
        if word in main:
            main = main.split(word)[0] + " "
    names.append(clean_words(main))

    # Add the full name for an abbreviation, or the abbreviation for a full name.
    twins = []
    for name in names:
        if name in ABBREVIATIONS:
            twins.append(ABBREVIATIONS[name])
        for short, full in ABBREVIATIONS.items():
            if name == full:
                twins.append(short)
    unique_names = []
    for name in names + twins:
        if name and name not in unique_names:
            unique_names.append(name)
    return unique_names


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence. A dot not followed by a space (v1.1) does not end one."""
    spans, start = [], 0
    for gap in re.finditer(r"(?<=[.!?])\s+", text):
        spans.append((start, gap.start()))
        start = gap.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def find_name(name: str, text: str) -> int | None:
    """Where the name first appears as whole words (any dash or space between words), or None."""
    pattern = r"[\W_]+".join(re.escape(word) for word in name.split())
    if not name.strip():
        return None
    found = re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text, re.IGNORECASE)
    return found.start() if found else None


def _role_cues(sentence: str) -> set[Literal["primary", "secondary"]]:
    """Keep both cues so the caller can detect conflicting roles."""
    cues: set[Literal["primary", "secondary"]] = set()
    for role in ("primary", "secondary"):
        if re.search(rf"\b{role}\b", sentence, re.IGNORECASE):
            cues.add(role)
    return cues


def match_one(outcome: RegisteredOutcome, paper: Paper) -> AbstractMatch:
    """Look for one registered outcome in one paper's abstract."""
    base = {
        "nct_id": outcome.nct_id,
        "pmid": paper.pmid,
        "role": outcome.role,
        "position": outcome.position,
        "measure_text": outcome.measure_text,
    }
    if not paper.abstract or not paper.abstract.strip():
        return AbstractMatch(**base, status="no_text")                  # we could not look: unknown, not "not identified"

    abstract = paper.abstract
    # Search each sentence so repeated mentions can contribute role cues.
    spans = sentence_spans(abstract)
    hits = []  # (candidate name, sentence start, sentence end)
    for name in candidate_names(outcome.measure_text):
        for start, end in spans:
            if find_name(name, abstract[start:end]) is not None:
                hits.append((name, start, end))
    if not hits:
        return AbstractMatch(**base, status="not_identified")

    name, start, end = hits[0]
    quote = TextQuote(text=abstract[start:end], start=start, end=end)
    cues = set()
    for _, sentence_start, sentence_end in hits:
        cues.update(_role_cues(abstract[sentence_start:sentence_end]))

    if len(cues) > 1:
        return AbstractMatch(**base, status="ambiguous", matched_name=name, quote=quote)
    return AbstractMatch(**base, status="identified", matched_name=name, quote=quote,
                         reported_role=next(iter(cues), None))


def match_outcomes_in_abstract(outcomes: list[RegisteredOutcome], paper: Paper) -> list[AbstractMatch]:
    """One AbstractMatch per registered outcome, in the registered order."""
    return [match_one(outcome, paper) for outcome in outcomes]


def quote_is_in_text(quote: TextQuote, paper: Paper) -> bool:
    """The check an LLM's quotes will face too: the quoted text sits exactly where it says it does."""
    return paper.abstract is not None and paper.abstract[quote.start:quote.end] == quote.text
