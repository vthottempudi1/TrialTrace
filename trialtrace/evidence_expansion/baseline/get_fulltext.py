##############################################################################
# trialtrace/evidence_expansion/baseline/get_fulltext.py
#
# PURPOSE: Get the full text of a paper, but only when PubMed Central has it
#          as open access with a body. Anything else is "not available".
#          We never scrape other sites.
#
# USE:     text = get_fulltext(http, settings, pmid)      # str, or None
##############################################################################

import json
import re
import time
import xml.etree.ElementTree as ET

import httpx

from trialtrace.config import Settings

WAIT_SECONDS = 0.4
ID_CONVERTER = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"


def find_pmcid(http: httpx.Client, settings: Settings, pmid: str) -> str | None:
    """The PubMed Central ID for a PMID, or None if the paper is not in PMC."""
    time.sleep(WAIT_SECONDS)
    try:
        response = http.get(ID_CONVERTER, params={"format": "json", "ids": pmid},
                            timeout=settings.request_timeout_seconds)
        records = json.loads(response.content)["records"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    if not records:
        return None
    return records[0].get("pmcid")


def xml_to_text(xml_bytes: bytes) -> str | None:
    """Join the paragraphs of the article body into one text. None if there is no body."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    body = root.find(".//body")
    if body is None:
        return None
    paragraphs = []
    for paragraph in body.iter("p"):
        text = " ".join("".join(paragraph.itertext()).split())
        if text:
            paragraphs.append(text)
    if not paragraphs:
        return None
    return "\n".join(paragraphs)


def get_fulltext(http: httpx.Client, settings: Settings, pmid: str) -> str | None:
    """Open-access full text as plain text (one paragraph per line), or None."""
    pmcid = find_pmcid(http, settings, pmid)
    if pmcid is None:
        return None
    url = f"{settings.pubmed_base_url}/efetch.fcgi"
    time.sleep(WAIT_SECONDS)
    try:
        response = http.get(url, params={"db": "pmc", "id": pmcid, "retmode": "xml"},
                            timeout=settings.request_timeout_seconds * 3)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return xml_to_text(response.content)
