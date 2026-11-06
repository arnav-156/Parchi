import pytest

from parchi.translit import key, phrase_key, romanise

STT_SAMPLE = ("भैया, Parle-G के दो carton, Maggi छोटा वाला पाँच डब्बा, Surf Excel एक किलो तीन packet। "
              "और Tata नमक बीस packet भेज देना।")


def test_romanise_keeps_latin_and_lowercases():
    r = romanise("Parle-G ke DO carton")
    assert r.text == "parle-g ke do carton"


def test_romanise_devanagari_casual_spelling():
    assert romanise("दो डब्बा").text == "do dabbaa"
    assert romanise("एक नमक").text == "ek namak"  # final schwa dropped


def test_offsets_map_back_to_original_words():
    r = romanise(STT_SAMPLE)
    for roman_word, orig_word in [("chhotaa", "छोटा"), ("denaa", "देना"), ("maggi", "Maggi"), ("biis", "बीस")]:
        i = r.text.index(roman_word)
        s, e = r.to_orig(i, i + len(roman_word))
        assert STT_SAMPLE[s:e] == orig_word


def test_danda_becomes_full_stop():
    assert romanise("तीन packet।").text == "tiin packet."


def test_devanagari_digits():
    assert romanise("२० packet").text == "20 packet"


@pytest.mark.parametrize("latin,deva", [
    ("paanch", "पांच"), ("panch", "पाँच"), ("chhota", "छोटा"), ("wala", "वाला"), ("teen", "तीन"),
    ("bees", "बीस"), ("dedh", "डेढ़"), ("dhai", "ढाई"), ("ladi", "लड़ी"), ("darjan", "दर्जन"), ("chhah", "छह"),
])
def test_latin_and_devanagari_converge_to_same_key(latin, deva):
    assert phrase_key(latin) == phrase_key(deva)


def test_key_folds_spelling_variants():
    assert key("chhota") == key("chota")
    assert key("paanch") == key("panch")
    assert key("wala") == key("vala")
    assert key("100") == "100"  # digits are not collapsed
