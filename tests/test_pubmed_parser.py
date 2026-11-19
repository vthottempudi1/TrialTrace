##############################################################################
# tests/test_pubmed_parser.py
#
# PURPOSE: Check that PubMed XML becomes the right Papers, and that missing
#          parts stay None. Uses small inline XML, no network.
##############################################################################

import pytest

from trialtrace.parse.pubmed_parser import parse_pubmed_xml
from trialtrace.parse.registry_parser import ParseError


def article(pmid="111", abstract="", banks="", pub_date="<Year>2020</Year>", title="A title"):
    """One <PubmedArticle> as text. Pass an empty string to leave a part out."""
    pmid_xml = f"<PMID>{pmid}</PMID>" if pmid else ""
    return f"""<PubmedArticle><MedlineCitation>{pmid_xml}<Article>
      <Journal><Title>J Test</Title><JournalIssue><PubDate>{pub_date}</PubDate></JournalIssue></Journal>
      <ArticleTitle>{title}</ArticleTitle>{abstract}{banks}
    </Article></MedlineCitation></PubmedArticle>"""


def xml(*articles, root="PubmedArticleSet"):
    return f"<{root}>{''.join(articles)}</{root}>".encode()


def bank(name, *numbers):
    items = "".join(f"<AccessionNumber>{n}</AccessionNumber>" for n in numbers)
    return f"<DataBankList><DataBank><DataBankName>{name}</DataBankName><AccessionNumberList>{items}</AccessionNumberList></DataBank></DataBankList>"


def test_labelled_abstract_parts_are_joined_in_order():
    abstract = "<Abstract><AbstractText Label='BACKGROUND'>First.</AbstractText><AbstractText Label='METHODS'>Second.</AbstractText></Abstract>"
    [paper] = parse_pubmed_xml(xml(article(abstract=abstract)))
    assert paper.abstract == "First.\nSecond."


def test_no_abstract_is_none():
    [paper] = parse_pubmed_xml(xml(article()))
    assert paper.abstract is None


def test_text_inside_italics_is_kept():
    abstract = "<Abstract><AbstractText>Gene <i>TP53</i> mutated.</AbstractText></Abstract>"
    [paper] = parse_pubmed_xml(xml(article(abstract=abstract)))
    assert paper.abstract == "Gene TP53 mutated."


def test_clinicaltrials_ids_are_listed():
    [paper] = parse_pubmed_xml(xml(article(banks=bank("ClinicalTrials.gov", "NCT04644237"))))
    assert paper.listed_nct_ids == ["NCT04644237"]


def test_other_databanks_are_ignored():
    [paper] = parse_pubmed_xml(xml(article(banks=bank("GENBANK", "AB123456"))))
    assert paper.listed_nct_ids == []


def test_mistyped_id_is_kept_not_dropped():
    [paper] = parse_pubmed_xml(xml(article(banks=bank("ClinicalTrials.gov", "NCT123"))))
    assert paper.listed_nct_ids == ["NCT123"]


def test_year_falls_back_to_medline_date():
    [paper] = parse_pubmed_xml(xml(article(pub_date="<MedlineDate>2020 Jan-Feb</MedlineDate>")))
    assert paper.year == "2020 Jan-Feb"


def test_missing_pub_date_is_none():
    [paper] = parse_pubmed_xml(xml(article(pub_date="")))
    assert paper.year is None


def test_papers_come_back_in_file_order():
    papers = parse_pubmed_xml(xml(article(pmid="2"), article(pmid="1")))
    assert [p.pmid for p in papers] == ["2", "1"]


def test_missing_pmid_raises():
    with pytest.raises(ParseError):
        parse_pubmed_xml(xml(article(pmid="")))


def test_broken_xml_raises():
    with pytest.raises(ParseError):
        parse_pubmed_xml(b"<PubmedArticleSet><PubmedArticle>")


def test_wrong_root_raises():
    with pytest.raises(ParseError):
        parse_pubmed_xml(xml(article(), root="Other"))
