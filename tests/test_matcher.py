import pytest

from parchi.catalog import load_catalog, pack_amount, stt_keywords
from parchi.matcher import match_clarification, match_item
from parchi.parser import parse

CAT = load_catalog()
EX1 = "Parle-G ke do carton, Maggi chhota wala paanch dabba, Surf Excel ek kilo teen packet, Tata namak bees packet."
EX5 = "Parle-G के दो carton, Maggi छोटा वाला पांच डब्बा, Surf Excel एक किलो तीन packet, Tata नमक बीस packet."


def run(text):
    return [match_item(it, CAT) for it in parse(text).items]


def brief(ms):
    return [(m.sku_id, m.qty, m.unit, m.status) for m in ms]


EX1_EXPECTED = [
    ("BIS001", 2, "CARTON", "GREEN"),   # Parle-G
    ("NOO001", 5, "BOX", "GREEN"),      # Maggi single
    ("DET002", 3, "PACKET", "GREEN"),   # Surf Excel 1kg
    ("SAL001", 20, "PACKET", "GREEN"),  # Tata Salt 1kg
]


def test_example1():
    assert brief(run(EX1)) == EX1_EXPECTED


def test_example2_colgate_dozen_ambiguous():
    (m,) = run("Aur Colgate pichli baar kam aaya tha, do darjan bhej dena.")
    assert (m.qty, m.unit, m.status, m.reasons) == (24, "PIECE", "AMBER", ["ambiguous_variant"])
    assert m.candidates == ["TP001", "TP002"]


def test_example3():
    thums, goodday = run("Bhaiya dedh peti Thums Up aur Good Day bhi dena.")
    assert (thums.sku_id, thums.qty, thums.unit, thums.status) == ("BEV001", 1.5, "CARTON", "GREEN")
    assert (goodday.sku_id, goodday.status, goodday.reasons) == ("BIS002", "AMBER", ["qty_missing"])


def test_example4_clarification_200_wala():
    m = match_clarification(parse("200 wala").items, ["TP001", "TP002"], 24, "PIECE", CAT)
    assert (m.sku_id, m.qty, m.unit, m.status) == ("TP002", 24, "PIECE", "GREEN")


def test_example5_devanagari_identical_to_example1():
    assert brief(run(EX5)) == EX1_EXPECTED


def test_real_prisma_transcript_identical_to_example1():
    t = ("भैया, Parle-G के दो carton, Maggi छोटा वाला पाँच डब्बा, Surf Excel एक किलो तीन packet। "
         "और Tata नमक बीस packet भेज देना।")
    assert brief(run(t)) == EX1_EXPECTED


@pytest.mark.parametrize("text,sku", [
    ("Maggi bada wala chaar packet", "NOO002"),
    ("Surf Excel aadha kilo do packet", "DET001"),
    ("Colgate sau gram wala das piece", "TP001"),
    ("neela Colgate do piece", "TP003"),
    ("peela Lays paanch packet", "SNK004"),
    ("blue lays paanch packet", "SNK003"),
    ("Aashirvaad atta das kilo wala ek packet", "ATT002"),
    ("Fortune sarson ek peti", "OIL002"),
    ("Fortune ek peti", "OIL001"),
    ("Clinic Plus do ladi", "SHA001"),
])
def test_variant_cues_pick_one_sku(text, sku):
    (m,) = run(text)
    assert (m.sku_id, m.status) == (sku, "GREEN")


def test_cross_brand_ambiguity_keeps_candidates():
    (m,) = run("Tata paanch packet")
    assert m.status == "AMBER" and "ambiguous_variant" in m.reasons
    assert {"TEA001", "TEA002"} <= set(m.candidates)


def test_unmatched_is_red():
    (m,) = run("xyz kuch teen packet")
    assert (m.status, m.reasons, m.sku_id) == ("RED", ["unmatched"], None)


def test_low_confidence_is_amber():
    (m,) = run("surfexel ek kilo do packet")
    assert m.sku_id == "DET002" and m.status == "AMBER" and "low_confidence" in m.reasons


def test_suspicious_quantity():
    (m,) = run("Parle-G saadhe paanch sau carton")
    assert m.qty == 550 and "qty_suspicious" in m.reasons


def test_unit_mismatch_kg_for_toothpaste():
    (m,) = run("Colgate sau gram wala do kilo")
    assert m.sku_id == "TP001" and "unit_mismatch" in m.reasons


def test_dozen_converted_for_piece_sku_only():
    (lux,) = run("Lux do darjan")
    assert (lux.qty, lux.unit) == (24, "PIECE")
    (pg,) = run("Parle-G do darjan")  # sale unit PACKET: keep DOZEN
    assert (pg.qty, pg.unit) == (2, "DOZEN")


def test_missing_unit_suggests_sale_unit_but_needs_review():
    (m,) = run("Good Day paanch")
    assert (m.qty, m.unit, m.status, m.reasons) == (5, "PACKET", "AMBER", ["unit_missing"])


def test_junk_around_brand_is_not_green():
    (m,) = run("Tata Salt thai cotton synthol teen darjan")
    assert m.sku_id == "SAL001" and m.status == "AMBER" and "low_confidence" in m.reasons


def test_clarification_bada_wala():
    m = match_clarification(parse("bada wala").items, ["NOO001", "NOO002"], 5, "PACKET", CAT)
    assert (m.sku_id, m.status) == ("NOO002", "GREEN")


def test_clarification_gives_quantity():
    m = match_clarification(parse("paanch packet bhej do").items, ["BIS002"], None, None, CAT)
    assert (m.sku_id, m.qty, m.unit, m.status) == ("BIS002", 5, "PACKET", "GREEN")


def test_clarification_unhelpful_reply_stays_amber():
    m = match_clarification(parse("haan theek hai").items, ["TP001", "TP002"], 24, "PIECE", CAT)
    assert m.status == "AMBER" and m.candidates == ["TP001", "TP002"]


def test_pack_amount():
    assert pack_amount("100g") == (100, "mass")
    assert pack_amount("1kg") == (1000, "mass")
    assert pack_amount("750ml") == (750, "vol")
    assert pack_amount("1L") == (1000, "vol")


def test_stt_keywords_limit_brands_first_and_both_scripts():
    kw = stt_keywords(CAT)
    assert len(kw) <= 200 and len({k.lower() for k in kw}) == len(kw)
    assert kw[0] == "Parle-G" and "Colgate" in kw[:70]
    assert any("ऀ" <= ch <= "ॿ" for k in kw for ch in k)


# ---------- regressions from the synthetic phone-call test run (real Prisma transcripts) ----------

def lines_of(transcript):
    from parchi.pipeline import analyse
    return [(m.sku_id, m.qty, m.unit, m.status) for _, m in analyse(transcript, CAT).lines]


def test_call_greeting_is_not_an_order_line():
    t = ("नमस्ते भैया, श्यामा general store से बोल रहा हूँ। Tide के दो carton, Vim bar तीन dozen। "
         "Bisleri एक पेटी और Kurkure Biscuits पैकेट भेज देना।")
    assert lines_of(t) == [("DET005", 2, "CARTON", "GREEN"), ("DET007", 36, "PIECE", "GREEN"),
                           ("WAT001", 1, "CARTON", "GREEN"), ("SNK005", None, "PACKET", "AMBER")]


def test_garbled_quantity_keeps_the_product_line():
    # "pachees dabbe" heard as "पैची ढाबे": Parle-G must stay visible (AMBER), not vanish as chatter
    out = lines_of("साढ़े तीन पेटी Thums Up, पौने दो पेटी Sprite और Parle-G के पैची ढाबे।")
    assert out[:2] == [("BEV001", 3.5, "CARTON", "GREEN"), ("BEV003", 1.75, "CARTON", "GREEN")]
    assert out[2][0] == "BIS001" and out[2][3] == "AMBER"


def test_english_call():
    t = ("Hello, this is Joseph Daily Needs. Please send two cartons of Coca-Cola, five packets of Good Day, "
         "and one dozen Dove soap.")
    assert lines_of(t) == [("BEV002", 2, "CARTON", "GREEN"), ("BIS002", 5, "PACKET", "GREEN"),
                           ("SOP005", 12, "PIECE", "GREEN")]


def test_is_baar_complaint_call():
    t = "हाँ जी, गुप्ता किराना से। पिछली बार Surf Excel खराब आया था, इस बार Surf Excel तीन carton। Tata Tea दो डब्बा और Maggi भी भेज देना।"
    assert [(s, q, u) for s, q, u, _ in lines_of(t)] == [(None, 3, "CARTON"), (None, 2, "BOX"), (None, None, None)]
