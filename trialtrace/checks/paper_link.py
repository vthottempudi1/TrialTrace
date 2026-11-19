##############################################################################
# trialtrace/checks/paper_link.py
#
# PURPOSE: A PubMed search hit is only a candidate. Decide how well one paper
#          is tied to one trial, from what the paper itself says. Plain code.
#            listed         the paper's ID list names the trial (strongest)
#            text_only      the trial ID appears in the title/abstract, not in the ID list
#            not_confirmed  neither: the search matched something else, treat as unlinked
#          This is NOT "reports the results": that stays a later, reviewed step.
#          `shared` = the paper lists other trials too, so it may cover several.
#
# USE:     link = check_paper_link(paper, "NCT04644237")
##############################################################################

import re
from typing import Literal

from pydantic import BaseModel, Field

from trialtrace.contracts import NCT_ID_PATTERN
from trialtrace.parse.pubmed_parser import Paper


class PaperLink(BaseModel):
    """How well one paper is tied to one trial."""

    pmid: str
    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    status: Literal["listed", "text_only", "not_confirmed"]
    other_nct_ids: list[str] = Field(default_factory=list)    # other trials the paper lists
    shared: bool = False                                      # True if other_nct_ids is not empty
    reason: str                                               # one plain sentence, for the report


def mentions(text: str | None, nct_id: str) -> bool:
    """True if the exact ID appears in the text (NCT0123456 must not match NCT01234567)."""
    if text is None:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){nct_id}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def check_paper_link(paper: Paper, nct_id: str) -> PaperLink:
    """Compare one paper with the trial it was found for."""
    listed = {n.strip().upper() for n in paper.listed_nct_ids}
    others = sorted(listed - {nct_id})

    if nct_id in listed:
        status, reason = "listed", "the paper's ID list names this trial"
    elif mentions(paper.title, nct_id) or mentions(paper.abstract, nct_id):
        status, reason = "text_only", "the trial ID appears in the title or abstract but not in the paper's ID list"
    else:
        status, reason = "not_confirmed", "the trial ID was not found in the ID list, title or abstract"

    return PaperLink(pmid=paper.pmid, nct_id=nct_id, status=status,
                     other_nct_ids=others, shared= bool(others), reason=reason)   # blank 3: when is it shared?
