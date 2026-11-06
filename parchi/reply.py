"""Order → reply text (templates) → Timbre v2.5 speech (.ogg/opus for WhatsApp + .wav for the UI).

One Timbre call per distinct reply text (cached); the .wav is converted locally with ffmpeg, not
a second API call.
"""
from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from parchi import audio, config, db, gnani_client, templates
from parchi.catalog import Catalog, load_catalog
from parchi.export import to_base_qty

VOICES = {"hi-IN": config.TTS_VOICE, "en-IN": config.TTS_VOICE_EN}


@dataclass
class ReplyDraft:
    text: str
    lang: str
    lang_fallback: bool
    kind: str  # confirmation | clarification
    questions: list[str]


def question_for(line: dict, lang: str, cat: Catalog) -> str | None:
    """The question to ask the retailer about one AMBER line, or None if they can't answer it."""
    if line["status"] != "AMBER" or line["removed"]:
        return None
    if "ambiguous_variant" in line["reasons"]:
        cands = [cat.by_id[c] for c in line["candidates"] if c in cat.by_id]
        return templates.variant_question(cands, line["product_text"] or "", lang)
    if "qty_missing" in line["reasons"] and line["sku_id"]:
        return templates.qty_question(templates.spoken_name(cat.by_id[line["sku_id"]], cat, lang), lang)
    return None


def draft_reply(conn: sqlite3.Connection, order_id: int, include_total: bool = False,
                cat: Catalog | None = None) -> ReplyDraft:
    cat = cat or load_catalog()
    order = db.row(conn, "orders", order_id)
    assert order is not None
    retailer = db.row(conn, "retailers", order["retailer_id"])
    note = conn.execute("SELECT detected_lang FROM voice_notes WHERE order_id = ? AND kind = 'order' "
                        "AND detected_lang IS NOT NULL ORDER BY id LIMIT 1", (order_id,)).fetchone()
    lang, fallback = templates.reply_language(note[0] if note else None)

    lines, questions, total = [], [], 0.0
    for ln in db.order_lines(conn, order_id):
        if ln["removed"]:
            continue
        if ln["status"] == "GREEN":
            sku = cat.by_id[ln["sku_id"]]
            lines.append(templates.line_text(sku, ln["qty"], ln["unit"], cat, lang))
            total += to_base_qty(ln["qty"], ln["unit"], sku) * sku.mrp_inr
        elif q := question_for(ln, lang, cat):
            questions.append(q)
    text = templates.render_reply(
        name=retailer["name"] if retailer else "", lang=lang, lines=lines, questions=questions,
        total=total if include_total and total else None,
    )
    return ReplyDraft(text, lang, fallback, "clarification" if questions else "confirmation", questions)


def to_wav(ogg: Path) -> Path:
    wav = ogg.with_suffix(".wav")
    if not wav.exists():
        r = subprocess.run([audio.ffmpeg_bin(), "-v", "error", "-y", "-i", str(ogg), "-ar", "24000", "-ac", "1", str(wav)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg could not convert reply audio: {r.stderr.strip()[:200]}")
    return wav


def speak(conn: sqlite3.Connection, order_id: int, draft: ReplyDraft) -> int:
    """Synthesize the draft with Timbre v2.5 and store it. Raises gnani_client.GnaniError on API failure."""
    ogg = gnani_client.tts(draft.text, voice=VOICES[draft.lang], language=draft.lang)
    wav = to_wav(ogg)
    rid = db.insert(conn, "replies", order_id=order_id, kind=draft.kind, text=draft.text, lang=draft.lang,
                    lang_fallback=int(draft.lang_fallback), ogg_path=str(ogg), wav_path=str(wav), created_at=db.now())
    db.log_event(conn, "reply_spoken", order_id, reply_id=rid, reply_kind=draft.kind, lang=draft.lang)
    return rid
