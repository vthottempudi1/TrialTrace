##############################################################################
# trialtrace/evidence_expansion/eval/table_audit.py
#
# PURPOSE: Audit step. For outcomes the gold marked "not identified", look in the TABLES of
#          every open-access paper we have for that trial. Count tables that mention the
#          outcome's words, and save them so a person (or the model) can read them.
#          Tables that are only pictures cannot be read and are counted separately.
#
# USE:     python -m trialtrace.evidence_expansion.eval.table_audit
##############################################################################

import json
import time
import xml.etree.ElementTree as ET

import httpx
import psycopg

from trialtrace.config import Settings
from trialtrace.evidence_expansion.baseline.get_fulltext import find_pmcid, xml_to_tables
from trialtrace.evidence_expansion.baseline.pick_passages import outcome_words
from trialtrace.evidence_expansion.eval.load_cases import load_case, read_sheet
from trialtrace.evidence_expansion.eval.source_cache import SourceCache

GOLD_PATH = "outputs/triage/gold.json"
OUT_PATH = "outputs/triage/table_audit.json"
MIN_WORDS_SHARED = 2


def fetch_xml(http: httpx.Client, settings: Settings, pmid: str) -> bytes | None:
    pmcid = find_pmcid(http, settings, pmid)
    if pmcid is None:
        return None
    time.sleep(0.4)
    response = http.get(f"{settings.pubmed_base_url}/efetch.fcgi", params={"db": "pmc", "id": pmcid, "retmode": "xml"},
                        timeout=60)
    if response.status_code != 200:
        return None
    return response.content


def count_picture_tables(xml_bytes: bytes) -> int:
    """Table wraps that have a graphic but no real table (a table saved as an image)."""
    root = ET.fromstring(xml_bytes)
    count = 0
    for wrap in root.iter("table-wrap"):
        if wrap.find(".//table") is None and wrap.find(".//graphic") is not None:
            count += 1
    return count


def table_matches(table: dict, words: set[str], needed: int) -> bool:
    text = " ".join([table["caption"], " ".join(table["headers"]), " ".join(table["rows"]), table["footnote"]]).lower()
    shared = 0
    for word in words:
        if word in text:
            shared += 1
    return shared >= min(needed, len(words))


def main() -> None:
    settings = Settings()
    gold = {item["id"]: item for item in json.load(open(GOLD_PATH, encoding="utf-8"))}
    audit = []
    with httpx.Client() as http, psycopg.connect(settings.database_url) as conn:
        cache = SourceCache(http, settings)
        for row in read_sheet():
            if gold[row["id"]]["status"] != "not_identified":
                continue
            case = load_case(conn, row)
            outcome = case["outcome"]
            words = outcome_words(outcome.measure_text)
            pmids = [paper.pmid for paper in case["linked_papers"]]
            for item in cache.get_companions(outcome.nct_id, case["trial_title"], pmids, only="nct_search"):
                pmids.append(item["paper"].pmid)
            papers_with_body = 0
            picture_tables = 0
            hits = []
            for pmid in pmids:
                if cache.get_text(pmid) is None:
                    continue                                   # no open-access body: nothing to audit
                papers_with_body += 1
                xml_bytes = fetch_xml(http, settings, pmid)
                if xml_bytes is None:
                    continue
                picture_tables += count_picture_tables(xml_bytes)
                for table in xml_to_tables(xml_bytes):
                    if table_matches(table, words, MIN_WORDS_SHARED):
                        hits.append({"pmid": pmid, "label": table["label"], "caption": table["caption"][:200],
                                     "headers": table["headers"][:2], "rows": table["rows"][:6],
                                     "footnote": table["footnote"][:200]})
            audit.append({"id": row["id"], "outcome": outcome.measure_text, "role": row["role"],
                          "papers_with_body": papers_with_body, "picture_tables": picture_tables, "table_hits": hits})
            print(row["id"], "papers with body:", papers_with_body, "| table hits:", len(hits), "| picture tables:", picture_tables)
    with open(OUT_PATH, "w", encoding="utf-8") as out_file:
        json.dump(audit, out_file, indent=1)


if __name__ == "__main__":
    main()
