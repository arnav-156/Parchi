"""Headless UI flow via Streamlit AppTest: Review edits → Approve → Export (no API, temp DB)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from parchi import config, db

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = db.connect(path)
    oid = db.insert(conn, "orders", retailer_id="R002", status="DRAFT", created_at=db.now())
    t = "Colgate पिछली बार कम आया था, दो Darjan भेज देना। Good Day भी देना। lace teen packet"
    nid = db.insert(conn, "voice_notes", order_id=oid, kind="order", filename="n.ogg", status="PARSED",
                    transcript=t, detected_lang="hi-IN", notes_json=["Colgate पिछली बार कम आया था"],
                    api_calls=0, latency_s=0.6, created_at=db.now())
    rows = [
        dict(product_text="colgate", sku_id=None, qty=24, unit="PIECE", status="AMBER",
             reasons=["ambiguous_variant"], candidates=["TP001", "TP002"], span_start=0, span_end=7),
        dict(product_text="good day", sku_id="BIS002", qty=None, unit=None, status="AMBER",
             reasons=["qty_missing"], candidates=["BIS002"], span_start=50, span_end=58),
        dict(product_text="lace", sku_id=None, qty=3, unit="PACKET", status="RED", reasons=["unmatched"],
             candidates=["SPC003", "SNK003", "SNK004"], span_start=69, span_end=85),
    ]
    ids = [db.insert(conn, "order_lines", order_id=oid, note_id=nid, line_no=i, score=90, **r)
           for i, r in enumerate(rows, 1)]
    yield conn, oid, ids
    conn.close()


def review(oid):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = "Review"
    at.session_state["order_id"] = oid
    return at.run()


def btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_review_resolve_approve_export(seeded):
    conn, oid, (colgate, goodday, lace) = seeded
    at = review(oid)
    assert not at.exception
    assert "Retailer note" in " ".join(w.value for w in at.warning)
    assert btn(at, "Approve order").disabled  # AMBER/RED lines block approval

    at.selectbox(key=f"sku{colgate}").set_value("TP002")  # pick variant from candidates
    at.number_input(key=f"qty{goodday}").set_value(5.0)
    at.selectbox(key=f"unit{goodday}").set_value("PACKET")
    at.checkbox(key=f"rm{lace}").check()  # clerk removes the unmatched line
    btn(at, "Save changes").click()
    at.run()
    assert not at.exception

    lines = {ln["id"]: ln for ln in db.order_lines(conn, oid)}
    assert (lines[colgate]["sku_id"], lines[colgate]["status"], lines[colgate]["resolved_by"]) == ("TP002", "GREEN", "clerk")
    assert (lines[goodday]["qty"], lines[goodday]["status"]) == (5, "GREEN")
    assert lines[lace]["removed"] == 1
    assert db.row(conn, "orders", oid)["status"] == "DRAFT"  # saving never approves

    btn(at, "Approve order").click()
    at.run()
    assert db.row(conn, "orders", oid)["status"] == "APPROVED"

    at.session_state["page"] = "Export"
    at.run()
    assert not at.exception and not at.error
    df = at.dataframe[0].value
    assert list(df["Item Code"]) == ["TP002", "BIS002"]


def test_ok_tick_confirms_complete_amber_line(seeded):
    conn, oid, (colgate, goodday, lace) = seeded
    db.update(conn, "order_lines", goodday, qty=5, unit="PACKET", reasons=["low_confidence"])
    at = review(oid)
    at.checkbox(key=f"ok{goodday}").check()
    btn(at, "Save changes").click()
    at.run()
    assert db.row(conn, "order_lines", goodday)["status"] == "GREEN"
    assert db.row(conn, "order_lines", colgate)["status"] == "AMBER"  # untouched lines stay flagged


def test_export_page_empty_without_approved_orders(seeded):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = "Export"
    at.run()
    assert not at.exception and "No approved orders" in at.info[0].value


def test_reply_page_shows_questions_and_speaks(seeded, tmp_path, monkeypatch):
    import subprocess

    from parchi import audio, gnani_client
    conn, oid, (colgate, goodday, lace) = seeded
    subprocess.run([audio.ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1",
                    "-c:a", "libopus", "-f", "ogg", str(tmp_path / "s.ogg")], check=True)
    monkeypatch.setattr(gnani_client, "_call_tts", lambda text, params: (tmp_path / "s.ogg").read_bytes())
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = "Reply & Clarify"
    at.session_state["order_id"] = oid
    at.run()
    assert not at.exception
    draft = at.info[0].value
    assert "Colgate kaunsa chahiye, ek sau gram ya do sau gram?" in draft and "Good Day kitna bhejna hai?" in draft
    btn(at, "🔊 Generate spoken reply").click()
    at.run()
    assert not at.exception and not at.error
    r = db.rows(conn, "SELECT * FROM replies WHERE order_id = ?", (oid,))
    assert len(r) == 1 and r[0]["kind"] == "clarification" and r[0]["wav_path"].endswith(".wav")


def test_evaluation_page_without_reports(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = "Evaluation"
    at.run()
    assert not at.exception and "No evaluation report yet" in at.info[0].value
