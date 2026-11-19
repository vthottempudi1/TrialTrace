##############################################################################
# trialtrace/parse/sponsor.py
#
# PURPOSE: Give sponsor name variants one shared key, so "Acme Pharma, Inc."
#          and "ACME PHARMA INC" count as one sponsor.
#          Deliberately conservative: only case, accents, punctuation and legal endings
#          (Inc, Ltd, Corp...) are ignored. Different words are never merged,
#          because wrongly merging two sponsors would make a false claim.
#          A missing name stays None (unknown), never an empty string.
#
# USE:     from trialtrace.parse.sponsor import normalize_sponsor
#          normalize_sponsor("Acme Pharma, Inc.")   # -> "acme pharma"
##############################################################################

import re
import unicodedata                                  # to split "ü" into "u" plus an accent mark

# Legal endings, ignored only at the END of a name
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "gmbh", "ag", "sa", "lp", "llp",
    "sl", "srl", "spa", "bv", "nv",                 # S.L., S.r.l., S.p.A., B.V., N.V. once their dots are removed
}

# A leftover "and" at the end ("Eli Lilly and Company" -> "eli lilly and") is dropped too
TRAILING_WORDS = LEGAL_SUFFIXES | {"and"}


def normalize_sponsor(name: str | None) -> str | None:
    """'Acme Pharma, Inc.' -> 'acme pharma'. None or nothing left -> None."""
    if name is None:
        return None

    # Accents are ignored: "Zürich" and "Zurich", "İSMAİL" and "Ismail" are the same sponsor
    letters_and_marks = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in letters_and_marks if not unicodedata.combining(ch))

    # Lowercase, delete dots ("S.L." -> "sl", "L.L.C." -> "llc"), then turn other punctuation into spaces
    text = re.sub(r"[\W_]+", " ", without_accents.lower().replace(".", ""))
    words = text.split()

    while words and words[-1] in TRAILING_WORDS:
        words.pop()                                    # drop the last word

    return " ".join(words) or None                     # nothing left = unknown
