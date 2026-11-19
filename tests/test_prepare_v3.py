from evals.prepare_v3 import pick, spans


def test_sentence_spans_keep_vs_comparison_and_original_offsets():
    text = "Rates were 10% vs. 20%. No difference was observed."
    pieces = [text[start:end] for start, end in spans(text)]
    assert pieces == ["Rates were 10% vs. 20%.", "No difference was observed."]


def test_sentence_spans_keep_decimal_values():
    text = "The mean was 1.25. Safety was similar."
    assert [text[s:e] for s, e in spans(text)] == ["The mean was 1.25.", "Safety was similar."]


def test_sentence_spans_keep_statistical_and_clock_abbreviations():
    text = "Mean change was 6.2% (s.d. 7.3). Average A.M. and P.M. scores improved."
    assert [text[s:e] for s, e in spans(text)] == [
        "Mean change was 6.2% (s.d. 7.3).", "Average A.M. and P.M. scores improved."
    ]


def candidate(nct, category, sentence):
    return ({"nct_id": nct}, {"reported": category, "sentence": sentence}, "fake")


def test_selection_does_not_reuse_positive_evidence_sentence():
    pool = [candidate("A", "yes", 1), candidate("A", "yes", 1), candidate("B", "yes", 2)]
    chosen, counts = pick(pool, {"yes": 2, "no": 0, "unclear": 0})
    assert len(chosen) == 2
    assert [item[0]["nct_id"] for item in chosen] == ["A", "B"]
    assert counts["yes"] == 2


def test_selection_caps_positive_cases_per_trial():
    pool = [candidate("A", "yes", i) for i in range(10)]
    chosen, counts = pick(pool, {"yes": 10, "no": 0, "unclear": 0})
    assert len(chosen) == 3


def test_selection_caps_total_cases_per_trial():
    pool = [candidate("A", "no", None) for _ in range(10)]
    chosen, counts = pick(pool, {"yes": 0, "no": 10, "unclear": 0})
    assert len(chosen) == 5
