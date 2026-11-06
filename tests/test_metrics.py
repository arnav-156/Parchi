from parchi.eval.metrics import aggregate, score_note

GOLD = [("BIS001", 2, "CARTON"), ("TP002", 24, "PIECE"), ("BIS002", 5, "PACKET")]


def test_perfect_note():
    s = score_note(GOLD, [(t, "GREEN") for t in GOLD])
    m = aggregate([s])
    assert (m["precision"], m["recall"], m["f1"], m["unsafe_auto_approve_rate"]) == (1, 1, 1, 0)


def test_line_correct_only_if_sku_qty_and_unit_match():
    pred = [
        (("BIS001", 2, "CARTON"), "GREEN"),  # right
        (("TP001", 24, "PIECE"), "GREEN"),   # wrong SKU, GREEN → unsafe
        (("BIS002", 5, "BOX"), "AMBER"),     # wrong unit
    ]
    s = score_note(GOLD, pred)
    assert (s.correct, s.green_wrong, s.sku_hits) == (1, 1, 2)
    m = aggregate([s])
    assert m["precision"] == m["recall"] == 1 / 3
    assert m["unsafe_auto_approve_rate"] == 0.5
    assert m["clarification_rate"] == 1 / 3
    assert m["sku_accuracy"] == 2 / 3 and m["qty_accuracy"] == 0.5


def test_ambiguous_line_without_sku_is_never_correct_and_not_unsafe():
    s = score_note(GOLD[:1], [((None, 24, "PIECE"), "AMBER"), (("BIS001", 2.0, "CARTON"), "GREEN")])
    assert (s.correct, s.green_wrong, s.amber) == (1, 0, 1)


def test_missed_and_extra_lines():
    s = score_note(GOLD, [(("BIS001", 2, "CARTON"), "GREEN"), ((None, 3, "PACKET"), "RED")])
    m = aggregate([s])
    assert m["recall"] == 1 / 3 and m["precision"] == 0.5 and m["red_rate"] == 0.5


def test_duplicate_gold_lines_count_as_multiset():
    gold = [("BIS001", 2, "CARTON")] * 2
    s = score_note(gold, [(("BIS001", 2, "CARTON"), "GREEN")] * 3)
    assert (s.correct, s.green_wrong) == (2, 1)


def test_aggregate_handles_empty():
    assert aggregate([])["f1"] == 0.0
