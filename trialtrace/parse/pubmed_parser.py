##############################################################################
# trialtrace/parse/pubmed_parser.py
#
# PURPOSE: Turn the raw PubMed XML (saved by Lesson 25) into Paper objects.
#          One XML file holds up to 20 papers; each <PubmedArticle> becomes one Paper.
#          Missing parts stay None (unknown), never guessed. Bad XML raises ParseError.
#          A paper that lists the trial's NCT ID is a CANDIDATE, not proof it reports results.
#
# USE:     papers = parse_pubmed_xml(fetch.body)
##############################################################################

import xml.etree.ElementTree as ET           # reads XML into a tree of elements

from pydantic import BaseModel, Field

from trialtrace.parse.registry_parser import ParseError    # same error type as the registry parser


class Paper(BaseModel):
    """One paper as PubMed describes it."""

    pmid: str = Field(pattern=r"^[0-9]{1,9}$")
    title: str | None = None                  # None = not in the record
    abstract: str | None = None               # None = no abstract (many papers have none)
    journal: str | None = None
    year: str | None = None                   # raw text, same precision rule as registry dates
    listed_nct_ids: list[str] = Field(default_factory=list)   # NCT IDs in the paper's ID list


def _text(element: ET.Element | None) -> str | None:
    """All text inside an element (including <i>..</i> parts), or None if missing/empty."""
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return text or None


def parse_article(article: ET.Element) -> Paper:
    """One <PubmedArticle> -> one Paper."""
    pmid = _text(article.find("MedlineCitation/PMID"))
    if pmid is None:
        raise ParseError("paper without a PMID")

    # abstract can be split in labeled parts (BACKGROUND, METHODS...): join them in order
    parts = [_text(p) for p in article.findall("MedlineCitation/Article/Abstract/AbstractText")]
    abstract = "\n".join( p for p in parts if p) or None          # blank 1: what do we join?

    # NCT IDs live under DataBank, only where the bank is ClinicalTrials.gov
    nct_ids = [
        _text(number)
        for bank in article.findall("MedlineCitation/Article/DataBankList/DataBank")
        if _text(bank.find("DataBankName")) == "ClinicalTrials.gov"                # blank 2: the bank's exact name
        for number in bank.findall("AccessionNumberList/AccessionNumber")
    ]

    # some records have <MedlineDate>2020 Jan-Feb</MedlineDate> instead of <Year>: keep that raw text
    pub_date = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate")
    year = _text(pub_date.find("Year")) or _text(pub_date.find("MedlineDate")) if pub_date is not None else None

    return Paper(
        pmid=pmid,
        title=_text(article.find("MedlineCitation/Article/ArticleTitle")),
        abstract=abstract,
        journal=_text(article.find("MedlineCitation/Article/Journal/Title")),
        year=year,
        listed_nct_ids=[n for n in nct_ids if n],
    )


def parse_pubmed_xml(body: bytes) -> list[Paper]:
    """The whole XML file -> a list of Papers, in file order."""
    try:
        root = ET.fromstring(body)            # fails on broken XML
    except ET.ParseError as error:
        raise ParseError(f"PubMed XML is not well formed: {error}") from error
    if root.tag != "PubmedArticleSet":                    # blank 4: the root element's name
        raise ParseError(f"unexpected root element: {root.tag!r}")
    return [parse_article(article) for article in root.findall("PubmedArticle")]
