##############################################################################
# trialtrace/evidence_expansion/eval/source_cache.py
#
# PURPOSE: Fetch each paper's full text and each trial's companion papers ONCE and keep
#          them in a JSON file. Every later run (gold, baseline, agent) reads the same
#          saved text, so the three configurations are compared on identical sources
#          and a run can be repeated without the internet.
#
# USE:     cache = SourceCache(http, settings)
#          text = cache.get_text("12345")                       # full text or None
#          companions = cache.get_companions(nct_id, title, linked_pmids)
##############################################################################

import json
import os

import httpx

from trialtrace.config import Settings
from trialtrace.evidence_expansion.baseline.find_papers import find_companion_papers, load_papers, search_words
from trialtrace.evidence_expansion.baseline.get_fulltext import get_fulltext
from trialtrace.parse.pubmed_parser import Paper

CACHE_PATH = "outputs/triage/cache.json"


class SourceCache:
    def __init__(self, http: httpx.Client, settings: Settings):
        self.http = http
        self.settings = settings
        self.data = {"texts": {}, "companions": {}, "searches": {}}
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding="utf-8") as cache_file:
                self.data = json.load(cache_file)
        if "searches" not in self.data:
            self.data["searches"] = {}

    def save(self) -> None:
        with open(CACHE_PATH, "w", encoding="utf-8") as cache_file:
            json.dump(self.data, cache_file)

    def get_text(self, pmid: str) -> str | None:
        """Open-access full text, fetched the first time and saved."""
        if pmid not in self.data["texts"]:
            self.data["texts"][pmid] = get_fulltext(self.http, self.settings, pmid)
            self.save()
        return self.data["texts"][pmid]

    def get_companions(self, nct_id: str, trial_title: str, linked_pmids: list[str], only: str | None = None) -> list[dict]:
        """Companion papers as [{"paper": Paper, "found_by": ...}], fetched the first time and saved.
        only="nct_search" keeps just the papers the NCT search found."""
        if nct_id not in self.data["companions"]:
            found = find_companion_papers(self.http, self.settings, nct_id, trial_title, linked_pmids)
            saved = []
            for item in found:
                paper = item["paper"]
                saved.append({"pmid": paper.pmid, "title": paper.title, "abstract": paper.abstract,
                              "found_by": item["found_by"], "listed_nct_ids": paper.listed_nct_ids})
            self.data["companions"][nct_id] = saved
            self.save()
        companions = []
        for item in self.data["companions"][nct_id]:
            if only is not None and item["found_by"] != only:
                continue
            paper = Paper(pmid=item["pmid"], title=item["title"], abstract=item["abstract"],
                          listed_nct_ids=item.get("listed_nct_ids", []))
            companions.append({"paper": paper, "found_by": item["found_by"]})
        return companions

    def companions(self, nct_id, trial_title, linked_pmids, only=None) -> list[dict]:
        return self.get_companions(nct_id, trial_title, linked_pmids, only)

    def search(self, query: str, linked_pmids: list[str]) -> list[dict]:
        """Papers from a free-word search, as [{"paper", "found_by": "query_search"}]. Fetched once, then saved."""
        if query not in self.data["searches"]:
            pmids = search_words(self.http, self.settings, query)
            pmids = [pmid for pmid in pmids if pmid not in linked_pmids]
            papers = load_papers(self.http, self.settings, "NCT00000000", pmids) if pmids else []
            saved = []
            for paper in papers:
                saved.append({"pmid": paper.pmid, "title": paper.title, "abstract": paper.abstract,
                              "listed_nct_ids": paper.listed_nct_ids})
            self.data["searches"][query] = saved
            self.save()
        results = []
        for item in self.data["searches"][query]:
            paper = Paper(pmid=item["pmid"], title=item["title"], abstract=item["abstract"],
                          listed_nct_ids=item.get("listed_nct_ids", []))
            results.append({"paper": paper, "found_by": "query_search"})
        return results

    def fill_missing_nct_ids(self) -> None:
        """Older saved companions have no listed NCT IDs. Fetch their records once and add them."""
        groups = list(self.data["companions"].values()) + list(self.data["searches"].values())
        missing = []
        for group in groups:
            for item in group:
                if "listed_nct_ids" not in item and item["pmid"] not in missing:
                    missing.append(item["pmid"])
        ids_by_pmid = {}
        for paper in load_papers(self.http, self.settings, "NCT00000000", missing):
            ids_by_pmid[paper.pmid] = paper.listed_nct_ids
        for group in groups:
            for item in group:
                if "listed_nct_ids" not in item:
                    item["listed_nct_ids"] = ids_by_pmid.get(item["pmid"], [])
        self.save()
