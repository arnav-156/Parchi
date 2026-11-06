import subprocess
from pathlib import Path

import pytest

from parchi import audio, db, gnani_client, reply, templates
from parchi.catalog import load_catalog

CAT = load_catalog()


@pytest.mark.parametrize("n,words", [
    (1, "ek"), (2, "do"), (20, "bees"), (99, "ninyanve"), (100, "ek sau"), (200, "do sau"), (150, "ek sau pachas"),
    (1000, "ek hazaar"), (2235, "do hazaar do sau paintis"), (100000, "ek lakh"),
    (125300, "ek lakh pachees hazaar teen sau"), (1500000, "pandrah lakh"),
])
def test_hindi_int_uses_lakh_grouping(n, words):
    assert templates.hindi_int(n) == words


@pytest.mark.parametrize("x,words", [
    (0.5, "aadha"), (1.5, "dedh"), (2.5, "dhai"), (3.5, "saadhe teen"), (2.25, "sawa do"), (4.75, "paune paanch"),
    (24, "chaubis"),
])
def test_hindi_qty_fractions(x, words):
    assert templates.hindi_qty(x) == words


def test_english_amount_lakh_form():
    assert templates.english_amount(125300) == "1 lakh 25 thousand 300"


def test_line_text_matches_brief():
    assert templates.line_text(CAT.by_id["BIS001"], 2, "CARTON", CAT, "hi-IN") == "Parle-G do carton"
    assert templates.line_text(CAT.by_id["DET002"], 3, "PACKET", CAT, "hi-IN") == "Surf Excel ek kilo teen packet"
    assert templates.line_text(CAT.by_id["BIS001"], 2, "CARTON", CAT, "en-IN") == "Parle-G 2 carton"


def test_variant_question_same_family():
    q = templates.variant_question([CAT.by_id["TP001"], CAT.by_id["TP002"]], "colgate", "hi-IN")
    assert q == "Colgate kaunsa chahiye, ek sau gram ya do sau gram?"
    q = templates.variant_question([CAT.by_id["NOO001"], CAT.by_id["NOO002"]], "maggi", "en-IN")
    assert q == "Which Maggi do you want, Single or 4-Pack?"


def test_variant_question_max_three_options_across_families():
    cands = [CAT.by_id[s] for s in ("TEA001", "TEA002", "DAL001", "SAL001")]
    q = templates.variant_question(cands, "tata", "hi-IN")
    assert q.startswith("Tata kaunsa chahiye, ") and q.count(",") == 2 and " ya " in q and "Salt" not in q


def test_qty_question():
    assert templates.qty_question("Good Day", "hi-IN") == "Good Day kitna bhejna hai?"


@pytest.mark.parametrize("detected,lang,fallback", [
    ("hi-IN", "hi-IN", False), ("en-IN", "en-IN", False), ("ta-IN", "hi-IN", True), (None, "hi-IN", True),
])
def test_reply_language_fallback(detected, lang, fallback):
    assert templates.reply_language(detected) == (lang, fallback)


def test_render_reply_with_total():
    text = templates.render_reply(name="Sharma General Store", lang="hi-IN", lines=["Parle-G do carton"],
                                  questions=["Good Day kitna bhejna hai?"], total=125300.4, distributor="X Traders")
    assert text == ("Sharma General Store ji, aapka order mila: Parle-G do carton. Good Day kitna bhejna hai? "
                    "Anumaanit total ek lakh pachees hazaar teen sau rupaye. Dhanyavaad, X Traders.")


# ---------- reply.py against a DB order ----------

@pytest.fixture
def order(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    oid = db.insert(conn, "orders", retailer_id="R002", status="DRAFT", created_at=db.now())
    nid = db.insert(conn, "voice_notes", order_id=oid, status="PARSED", detected_lang="hi-IN", created_at=db.now())
    for i, (sku, qty, unit, status, reasons, cands) in enumerate([
        ("BEV001", 1.5, "CARTON", "GREEN", [], ["BEV001"]),
        (None, 24, "PIECE", "AMBER", ["ambiguous_variant"], ["TP001", "TP002"]),
        ("BIS002", None, None, "AMBER", ["qty_missing"], ["BIS002"]),
        (None, 3, "PACKET", "RED", ["unmatched"], ["SNK003"]),
    ], 1):
        db.insert(conn, "order_lines", order_id=oid, note_id=nid, line_no=i, product_text="colgate", sku_id=sku,
                  qty=qty, unit=unit, status=status, reasons=reasons, candidates=cands)
    yield conn, oid
    conn.close()


def test_draft_reply_asks_clarifications(order):
    conn, oid = order
    d = reply.draft_reply(conn, oid)
    assert d.kind == "clarification" and d.lang == "hi-IN" and not d.lang_fallback
    assert d.text == ("Gupta Kirana ji, aapka order mila: Thums Up dedh carton. "
                      "Colgate kaunsa chahiye, ek sau gram ya do sau gram? Good Day kitna bhejna hai? "
                      "Dhanyavaad, Shree Ganesh Traders.")


def test_draft_reply_fallback_language_recorded(order):
    conn, oid = order
    conn.execute("UPDATE voice_notes SET detected_lang = 'bn-IN'")
    d = reply.draft_reply(conn, oid)
    assert (d.lang, d.lang_fallback) == ("hi-IN", True)


def test_speak_makes_ogg_and_wav_with_one_tts_call(order, tmp_path, monkeypatch):
    conn, oid = order
    subprocess.run([audio.ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1",
                    "-c:a", "libopus", "-f", "ogg", str(tmp_path / "s.ogg")], check=True)
    ogg_bytes = (tmp_path / "s.ogg").read_bytes()
    calls = []
    monkeypatch.setattr(gnani_client, "_call_tts", lambda text, params: calls.append(params) or ogg_bytes)
    d = reply.draft_reply(conn, oid)
    rid = reply.speak(conn, oid, d)
    reply.speak(conn, oid, d)  # same text again: cache hit
    r = db.row(conn, "replies", rid)
    assert len(calls) == 1 and calls[0]["model"] == "timbre-v2.5" and calls[0]["container"] == "ogg"
    assert r["ogg_path"].endswith(".ogg") and r["wav_path"].endswith(".wav") and r["kind"] == "clarification"
    assert audio.duration_s(Path(r["wav_path"])) > 0.5
