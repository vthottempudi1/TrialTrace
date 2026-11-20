from trialtrace.review.match_review import sort_by_risk


def test_ambiguous_first_then_identified_then_not_identified():
    rows = [{"status": "not_identified", "id": 1}, {"status": "identified", "id": 2},
            {"status": "ambiguous", "id": 3}, {"status": "identified", "id": 4}]
    assert [r["id"] for r in sort_by_risk(rows)] == [3, 2, 4, 1]      # ties keep their order


def test_unknown_status_counts_as_high_risk():
    rows = [{"status": "identified", "id": 1}, {"status": "weird", "id": 2}]
    assert [r["id"] for r in sort_by_risk(rows)] == [2, 1]
