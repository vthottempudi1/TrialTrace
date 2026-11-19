##############################################################################
# trialtrace/review/highlight.py
#
# PURPOSE: Show a finding's source quote inside the abstract it came from, so a reviewer sees the context.
#          The stored position is only trusted if the text at that position IS the quote; otherwise the quote is
#          looked for in the abstract; if it cannot be found, nothing is highlighted and that is said.
#          Also numbers the sentences of an abstract exactly as the blind labelling sheets do.
#
# USE:     parts = locate_quote(abstract, quote, start, end)      # (before, quote, after) or None
#          text = highlighted_markdown(abstract, quote, start, end)
#          text = numbered_markdown(abstract)                     # one sentence per line, [1] [2] ...
##############################################################################

import re

BACKSLASH = chr(92)
MARKDOWN_SPECIAL = BACKSLASH + "`*_{}[]<>#$~|"        # characters Streamlit markdown would read as formatting or maths
ABBREVIATION_END = re.compile(r"\b(?:vs|e\.g|i\.e|s\.d|s\.e|a\.m|p\.m|Dr|Mr|Mrs)\.$", re.IGNORECASE)
SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+")


def locate_quote(abstract: str | None, quote: str | None, start: int | None, end: int | None) -> tuple[str, str, str] | None:
    """Split the abstract into (before, quote, after), or None when the quote is not in it."""
    if not abstract or not quote:
        return None
    if start is not None and end is not None and abstract[start:end] == quote:
        return abstract[:start], quote, abstract[end:]
    found = abstract.find(quote)
    if found == -1:
        return None
    return abstract[:found], quote, abstract[found + len(quote):]


def escape_markdown(text: str) -> str:
    """Make text show literally in Streamlit markdown (no formatting, no maths)."""
    return "".join(BACKSLASH + character if character in MARKDOWN_SPECIAL else character for character in text)


def highlighted_markdown(abstract: str | None, quote: str | None, start: int | None, end: int | None) -> str | None:
    """The abstract as markdown with the quote highlighted, or None when it cannot be placed."""
    parts = locate_quote(abstract, quote, start, end)
    if parts is None:
        return None
    before, found, after = parts
    return f"{escape_markdown(before)}:orange-background[{escape_markdown(found)}]{escape_markdown(after)}"


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence positions in order. The same rule as the blind labelling sheets (evals/prepare_v3.py), so a
    reviewer's sentence numbers mean the same thing there and here."""
    start = 0
    result = []
    for gap in SENTENCE_GAP.finditer(text):
        if ABBREVIATION_END.search(text[start:gap.start()]):
            continue
        result.append((start, gap.start()))
        start = gap.end()
    if start < len(text):
        result.append((start, len(text)))
    return result


def numbered_markdown(abstract: str) -> str:
    """The abstract one sentence per paragraph, each starting with its number."""
    lines = [f"**[{number}]** {escape_markdown(abstract[start:end])}" for number, (start, end) in enumerate(sentence_spans(abstract), start=1)]
    return "\n\n".join(lines)
