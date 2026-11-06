import csv

import pytest

from parchi import config
from parchi.parser import lexicon, parse

EX1 = "Parle-G ke do carton, Maggi chhota wala paanch dabba, Surf Excel ek kilo teen packet, Tata namak bees packet."
EX5 = "Parle-G के दो carton, Maggi छोटा वाला पांच डब्बा, Surf Excel एक किलो तीन packet, Tata नमक बीस packet."


def summary(text):
    r = parse(text)
    return [(it.product_text, [(c.value, c.unit) for c in it.size_cues], it.qty, it.unit) for it in r.items]


# ---------- few-shot examples ----------

def test_example1():
    assert summary(EX1) == [
        ("parle-g", [], 2, "CARTON"),
        ("maggi chhota", [], 5, "BOX"),
        ("surf excel", [(1, "KG")], 3, "PACKET"),
        ("tata namak", [], 20, "PACKET"),
    ]


def test_example2_complaint_note_plus_line():
    r = parse("Aur Colgate pichli baar kam aaya tha, do darjan bhej dena.")
    assert len(r.notes) == 1 and "pichli baar kam aaya" in r.notes[0]
    assert [(i.product_text, i.qty, i.unit) for i in r.items] == [("colgate", 2, "DOZEN")]


def test_example3_fraction_and_missing_qty():
    assert summary("Bhaiya dedh peti Thums Up aur Good Day bhi dena.") == [
        ("thums up", [], 1.5, "CARTON"),
        ("good day", [], None, None),
    ]


def test_example4_clarification_size_cue():
    assert summary("200 wala") == [("", [(200, None)], None, None)]


def test_example5_devanagari_matches_example1_quantities():
    ex1, ex5 = summary(EX1), summary(EX5)
    assert [(q, u, cues) for _, cues, q, u in ex5] == [(q, u, cues) for _, cues, q, u in ex1]


def test_real_prisma_transcript_parses():
    t = ("भैया, Parle-G के दो carton, Maggi छोटा वाला पाँच डब्बा, Surf Excel एक किलो तीन packet। "
         "और Tata नमक बीस packet भेज देना।")
    assert [(q, u) for _, _, q, u in summary(t)] == [(2, "CARTON"), (5, "BOX"), (3, "PACKET"), (20, "PACKET")]


# ---------- numbers 1–100, every form in both scripts ----------

def _number_forms():
    with open(config.LEXICON_DIR / "numbers_hi.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for form in r["forms"].split("|"):
                yield form, float(r["value"])


@pytest.mark.parametrize("form,value", list(_number_forms()))
def test_every_number_form(form, value):
    items = parse(f"Good Day {form} packet").items
    assert [(i.qty, i.unit) for i in items] == [(value, "PACKET")]


def test_number_table_covers_1_to_100_in_both_scripts():
    vals_latin, vals_deva = set(), set()
    for form, v in _number_forms():
        (vals_deva if any("ऀ" <= ch <= "ॿ" for ch in form) else vals_latin).add(v)
    assert vals_latin >= set(map(float, range(1, 101)))
    assert vals_deva >= set(map(float, range(1, 101)))


def test_no_key_collisions_between_numbers_and_other_lexicons():
    lx = lexicon()
    assert not set(lx.numbers) & set(lx.units)
    assert not set(lx.numbers) & set(lx.fillers)
    assert not set(lx.fractions) & (set(lx.numbers) | set(lx.units))


def test_digits_and_decimals():
    assert summary("Good Day 12 packet")[0][2:] == (12, "PACKET")
    assert summary("Thums Up 1.5 peti")[0][2:] == (1.5, "CARTON")


# ---------- fractions ----------

@pytest.mark.parametrize("phrase,value", [
    ("aadha", 0.5), ("आधा", 0.5), ("dedh", 1.5), ("डेढ़", 1.5), ("dhai", 2.5), ("ढाई", 2.5),
    ("sawa do", 2.25), ("सवा दो", 2.25), ("saadhe teen", 3.5), ("साढ़े तीन", 3.5),
    ("paune paanch", 4.75), ("पौने पांच", 4.75), ("dedh sau", 150), ("do sau", 200), ("ek sau bees", 120),
])
def test_fractions(phrase, value):
    assert summary(f"Thums Up {phrase} peti")[0][2:] == (value, "CARTON")


# ---------- units ----------

@pytest.mark.parametrize("word,unit", [
    ("carton", "CARTON"), ("कार्टन", "CARTON"), ("peti", "CARTON"), ("पेटी", "CARTON"), ("case", "CARTON"),
    ("dabba", "BOX"), ("डब्बा", "BOX"), ("box", "BOX"), ("packet", "PACKET"), ("pack", "PACKET"),
    ("पैकेट", "PACKET"), ("darjan", "DOZEN"), ("दर्जन", "DOZEN"), ("dozen", "DOZEN"), ("piece", "PIECE"),
    ("pc", "PIECE"), ("पीस", "PIECE"), ("bori", "SACK"), ("बोरी", "SACK"), ("ladi", "STRIP"),
    ("लड़ी", "STRIP"), ("strip", "STRIP"),
])
def test_count_units(word, unit):
    assert summary(f"Lux paanch {word}")[0][2:] == (5, unit)


@pytest.mark.parametrize("phrase,cue", [
    ("sau gram", (100, "G")), ("100 ग्राम", (100, "G")), ("200 g", (200, "G")), ("500 ml", (500, "ML")),
])
def test_gram_and_ml_are_size_cues_not_quantities(phrase, cue):
    assert summary(f"Colgate {phrase} do piece") == [("colgate", [cue], 2, "PIECE")]


@pytest.mark.parametrize("word,unit", [("kilo", "KG"), ("किलो", "KG"), ("kg", "KG"), ("litre", "L"), ("लीटर", "L")])
def test_kilo_litre_quantity_when_nothing_follows(word, unit):
    assert summary(f"chini teen {word}")[0][2:] == (3, unit)


def test_kilo_is_size_cue_when_quantity_follows():
    assert summary("Aashirvaad atta paanch kilo do packet") == [("aashirvaad atta", [(5, "KG")], 2, "PACKET")]


# ---------- segmentation & fillers ----------

def test_qty_first_pattern():
    assert summary("do darjan Colgate") == [("colgate", [], 2, "DOZEN")]


def test_split_without_commas_on_new_qty_product_pattern():
    assert [(p, q, u) for p, _, q, u in summary("Parle-G do carton Maggi paanch packet")] == [
        ("parle-g", 2, "CARTON"), ("maggi", 5, "PACKET")]


def test_bhej_do_is_a_verb_not_two():
    assert summary("Good Day bhej do") == [("good day", [], None, None)]
    assert summary("Good Day de do") == [("good day", [], None, None)]


def test_ek_aur_is_separator():
    assert [p for p, *_ in summary("Lux do piece ek aur Dove teen piece")] == ["lux", "dove"]


def test_phir_and_devanagari_aur_split():
    assert [p for p, *_ in summary("Lux do piece phir Dove teen piece और Vim ek piece")] == ["lux", "dove", "vim"]


def test_quantity_after_comma_attaches_to_previous_product():
    assert summary("Parle-G, do carton") == [("parle-g", [], 2, "CARTON")]


def test_complaint_without_qty_creates_no_line():
    r = parse("Maggi pichli baar toota hua aaya tha.")
    assert r.items == [] and len(r.notes) == 1


def test_complaint_with_qty_and_product_creates_line():
    r = parse("Surf Excel kharab tha, paanch packet aur bhejna")
    assert r.notes and [(i.product_text, i.qty) for i in r.items] == [("surf excel", 5)]


def test_spans_cover_item_text():
    r = parse(EX1)
    assert r.roman.text[r.items[0].start:r.items[0].end] == "parle-g ke do carton"


def test_quantity_before_comma_carries_to_next_product():
    # real Prisma output: "dedh" heard as English "dead", comma before the product
    assert summary("dead पेटी, thumbs up और good day भी देना।") == [
        ("thumbs up", [], 1.5, "CARTON"), ("good day", [], None, None)]


def test_unclaimed_quantity_becomes_empty_line():
    assert summary("Parle-G do carton, teen packet") == [("parle-g", [], 2, "CARTON"), ("", [], 3, "PACKET")]


def test_compound_size_cue():
    assert summary("Thums Up saat sau pachaas ml wala do peti") == [("thums up", [(750, "ML")], 2, "CARTON")]


def test_one_letter_unit_slip_after_number():
    assert summary("Parle-G के तीन ढाबा")[0][2:] == (3, "BOX")
    assert summary("Vim paanch cartoon")[0][2:] == (5, "CARTON")


def test_carried_quantity_does_not_jump_past_other_products():
    # real STT: "...paanch darjan, tees packet Good Day, teen darjan Pepsi" with a misplaced comma
    s = summary("Dabur Red के पाँच दर्जन तीस पैकेट, Good Day तीन दर्जन Pepsi")
    assert ("pepsi", [], None, None) in s and ("", [], 30, "PACKET") in s
