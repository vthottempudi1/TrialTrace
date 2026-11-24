##############################################################################
# trialtrace/evidence_expansion/baseline/pick_passages.py
#
# PURPOSE: A full paper is too long to send to the model. Pick the few paragraphs
#          that share the most words with the registered outcome. Plain word
#          counting, nothing clever.
#
# USE:     passages = pick_passages(full_text, outcome_text, how_many=5)
##############################################################################

import re

SMALL_WORDS = {"the", "and", "for", "with", "from", "that", "were", "was", "percent", "participants",
               "number", "change", "score", "level", "levels", "measure", "baseline", "who", "had"}


def outcome_words(outcome_text: str) -> set[str]:
    """The meaningful lowercase words of an outcome, e.g. 'Heart rate' -> {'heart', 'rate'}."""
    words = set()
    for word in re.findall(r"[A-Za-z0-9\-]+", outcome_text.lower()):
        if len(word) < 3:
            continue
        if word in SMALL_WORDS:
            continue
        words.add(word)
    return words


def score_paragraph(paragraph: str, words: set[str]) -> int:
    """How many of the outcome's words appear in this paragraph."""
    text = paragraph.lower()
    score = 0
    for word in words:
        if word in text:
            score += 1
    return score


def pick_passages(full_text: str, outcome_text: str, how_many: int = 5) -> list[str]:
    """The best paragraphs, in the order they appear in the paper."""
    words = outcome_words(outcome_text)
    paragraphs = full_text.split("\n")

    scored = []
    for position, paragraph in enumerate(paragraphs):
        score = score_paragraph(paragraph, words)
        if score > 0:
            scored.append((score, position))

    scored.sort(key=lambda item: (-item[0], item[1]))     # best score first, earlier paragraph breaks ties
    best_positions = sorted(position for score, position in scored[:how_many])

    passages = []
    for position in best_positions:
        passages.append(paragraphs[position])
    return passages
