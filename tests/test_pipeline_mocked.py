"""Pipeline end-to-end with gnani_client's raw calls mocked (no API, real ffmpeg + SQLite)."""
import numpy as np
import pytest
import soundfile as sf

from parchi import audio, config, db, gnani_client, pipeline

ORDER = ("भैया, Parle-G के दो carton, Maggi छोटा वाला पाँच डब्बा। "
         "और Colgate पिछली बार कम आया था, दो दर्जन भेज देना। Good Day भी देना।")


class FakeAPIError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def tone(path, seconds, sr=22050, gaps_every=None):
    t = np.arange(int(seconds * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220 * t)
    if gaps_every:  # silent 0.5 s gaps so the chunker has somewhere to cut
        for g in np.arange(gaps_every, seconds, gaps_every):
            x[int(g * sr): int((g + 0.5) * sr)] = 0
    sf.write(str(path), x.astype(np.float32), sr)
    return path


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


class Calls(list):
    replies = {"default": ORDER}


@pytest.fixture
def stt_calls(monkeypatch):
    calls = Calls()
    calls.replies = {"default": ORDER}

    def fake(path, params):
        calls.append(params)
        return {"success": True, "transcript": calls.replies["default"], "detected_language_code": "hi-IN"}

    monkeypatch.setattr(gnani_client, "_call_stt", fake)
    return calls


def test_process_note_writes_statuses_and_notes(conn, tmp_path, stt_calls):
    order_id, note_id = pipeline.process_note(conn, tone(tmp_path / "a.wav", 3), "R001")
    note = db.row(conn, "voice_notes", note_id)
    assert note["status"] == "PARSED" and note["detected_lang"] == "hi-IN" and note["api_calls"] == 1
    assert any("पिछली बार" in n for n in note["notes_json"])
    lines = db.order_lines(conn, order_id)
    assert [(ln["sku_id"], ln["qty"], ln["unit"], ln["status"]) for ln in lines] == [
        ("BIS001", 2, "CARTON", "GREEN"),
        ("NOO001", 5, "BOX", "GREEN"),
        (None, 24, "PIECE", "AMBER"),
        ("BIS002", None, None, "AMBER"),
    ]
    s, e = lines[0]["span_start"], lines[0]["span_end"]
    assert note["transcript"][s:e] == "Parle-G के दो carton"
    # the STT request used catalog keywords + topic, prisma-v3.0, codemix
    p = stt_calls[0]
    assert p["model"] == "prisma-v3.0" and p["format"] == "codemix" and p["language_code"] == "indic-auto"
    assert 0 < len(p["keywords"]) <= 200 and p["topic"]


def test_reprocessing_same_audio_makes_zero_api_calls(conn, tmp_path, stt_calls):
    wav = tone(tmp_path / "a.wav", 3)
    pipeline.process_note(conn, wav, "R001")
    before = gnani_client.usage()["total"]
    _, note_id = pipeline.process_note(conn, wav, "R001")
    assert gnani_client.usage()["total"] == before == 1
    assert len(stt_calls) == 1 and db.row(conn, "voice_notes", note_id)["api_calls"] == 0


def test_transient_errors_retried_then_stt_failed(conn, tmp_path, monkeypatch):
    attempts = []

    def flaky(path, params):
        attempts.append(1)
        raise FakeAPIError(503)

    monkeypatch.setattr(gnani_client, "_call_stt", flaky)
    order_id, note_id = pipeline.process_note(conn, tone(tmp_path / "a.wav", 3), "R001")
    note = db.row(conn, "voice_notes", note_id)
    assert len(attempts) == 3 and gnani_client.usage()["total"] == 3
    assert note["status"] == "STT_FAILED" and pipeline.STT_FAILED_MSG in note["error"]
    assert db.order_lines(conn, order_id) == []


def test_retry_then_success(tmp_path, monkeypatch):
    n = []

    def once_flaky(path, params):
        n.append(1)
        if len(n) == 1:
            raise FakeAPIError(429)
        return {"success": True, "transcript": "ok"}

    monkeypatch.setattr(gnani_client, "_call_stt", once_flaky)
    assert gnani_client.stt(tone(tmp_path / "a.wav", 1))["transcript"] == "ok"
    assert len(n) == 2


def test_out_of_credit_not_retried(tmp_path, monkeypatch):
    n = []

    def no_credit(path, params):
        n.append(1)
        raise FakeAPIError(402)

    monkeypatch.setattr(gnani_client, "_call_stt", no_credit)
    with pytest.raises(gnani_client.GnaniError):
        gnani_client.stt(tone(tmp_path / "a.wav", 1))
    assert len(n) == 1


def test_credit_guard_refuses_new_calls(conn, tmp_path, stt_calls, monkeypatch):
    monkeypatch.setattr(config, "MAX_API_CALLS", 0)
    _, note_id = pipeline.process_note(conn, tone(tmp_path / "a.wav", 3), "R001")
    note = db.row(conn, "voice_notes", note_id)
    assert note["status"] == "STT_FAILED" and "limit" in note["error"] and stt_calls == []


def test_long_note_chunked_with_previous_text(tmp_path, stt_calls):
    wav = audio.normalise(tone(tmp_path / "long.wav", 65, gaps_every=12))
    chunks = audio.chunk(wav)
    assert len(chunks) == 3 and all(audio.duration_s(c) <= config.STT_MAX_CHUNK_S for c in chunks)
    tr = pipeline.transcribe(tmp_path / "long.wav")
    assert tr.api_calls == 3 and "previous_text" not in stt_calls[0] and stt_calls[1]["previous_text"]


def test_clarification_resolves_line(conn, tmp_path, stt_calls):
    order_id, _ = pipeline.process_note(conn, tone(tmp_path / "a.wav", 3), "R001")
    colgate = next(ln for ln in db.order_lines(conn, order_id) if ln["candidates"] == ["TP001", "TP002"])
    stt_calls.replies["default"] = "200 वाला"
    m = pipeline.process_clarification(conn, colgate["id"], tone(tmp_path / "reply.wav", 1.5))
    assert m is not None and m.status == "GREEN"
    line = db.row(conn, "order_lines", colgate["id"])
    assert (line["sku_id"], line["qty"], line["status"], line["resolved_by"]) == ("TP002", 24, "GREEN", "clarification")
    assert stt_calls[-1]["keywords"][:2] == ["Colgate", "Colgate Strong Teeth"]


def test_approve_blocked_until_lines_resolved(conn, tmp_path, stt_calls):
    order_id, _ = pipeline.process_note(conn, tone(tmp_path / "a.wav", 3), "R001")
    with pytest.raises(ValueError):
        db.approve_order(conn, order_id)
    for ln in db.unresolved_lines(conn, order_id):
        db.update(conn, "order_lines", ln["id"], removed=1)
    db.approve_order(conn, order_id)
    assert db.row(conn, "orders", order_id)["status"] == "APPROVED"


def test_tts_cached(tmp_path, monkeypatch):
    n = []
    monkeypatch.setattr(gnani_client, "_call_tts", lambda text, params: n.append(params) or b"OggS....")
    p1 = gnani_client.tts("Sharma ji, aapka order mila.")
    p2 = gnani_client.tts("Sharma ji, aapka order mila.")
    assert p1 == p2 and p1.suffix == ".ogg" and len(n) == 1
    assert n[0]["model"] == "timbre-v2.5"


def test_corrupt_audio_marks_note_failed_without_api_call(conn, tmp_path, stt_calls):
    bad = tmp_path / "broken.ogg"
    bad.write_bytes(b"not really audio")
    order_id, note_id = pipeline.process_note(conn, bad, "R001")
    note = db.row(conn, "voice_notes", note_id)
    assert note["status"] == "STT_FAILED" and pipeline.STT_FAILED_MSG in note["error"]
    assert stt_calls == [] and gnani_client.usage()["total"] == 0


def test_missing_api_key_is_a_clean_failure(conn, tmp_path, monkeypatch):
    from gnani.stt.exceptions import AuthenticationError

    def no_key(path, params):
        raise AuthenticationError("api_key is required")

    monkeypatch.setattr(gnani_client, "_call_stt", no_key)
    _, note_id = pipeline.process_note(conn, tone(tmp_path / "a.wav", 1), "R001")
    note = db.row(conn, "voice_notes", note_id)
    assert note["status"] == "STT_FAILED" and "api_key" in note["error"]
