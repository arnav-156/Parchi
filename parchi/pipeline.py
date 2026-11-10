"""Voice note → transcript → parsed, matched order lines (and clarification replies).

CLI:  python -m parchi.pipeline data/samples/sample1.ogg [--no-bias]
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from parchi import audio, config, db, gnani_client
from parchi.catalog import Catalog, load_catalog, stt_keywords
from parchi.matcher import MatchResult, match_clarification, match_item
from parchi.parser import LineItem, ParseResult, parse

STT_FAILED_MSG = "Audio attached — please listen"


@dataclass
class Transcript:
    text: str
    detected_lang: str | None
    api_calls: int
    cached: bool


@dataclass
class Analysis:
    parse: ParseResult
    lines: list[tuple[LineItem, MatchResult]] = field(default_factory=list)


def transcribe(audio_path: Path, *, keywords: list[str] | None = None, topic: str | None = None) -> Transcript:
    """Normalise, chunk (≤28 s) and transcribe with Prisma v3.0; chunks carry previous text as context."""
    before = gnani_client.usage()["total"]
    chunks = audio.chunk(audio.normalise(Path(audio_path)))
    texts: list[str] = []
    lang = None
    cached = True
    for c in chunks:
        prev = " ".join(texts)[-600:] or None
        r = gnani_client.stt(c, keywords=keywords, topic=topic, previous_text=prev)
        texts.append(r.get("transcript", "").strip())
        lang = lang or r.get("detected_language_code")
        cached = cached and r["_cached"]
    return Transcript(" ".join(t for t in texts if t), lang, gnani_client.usage()["total"] - before, cached)


def is_chatter(item: LineItem, m: MatchResult) -> bool:
    """A phrase with no quantity, unit or size that doesn't clearly name a product is talk, not an order line
    ("Sharma General Store se bol raha hoon"). Clear mentions like "Good Day bhi dena" are kept (qty_missing)."""
    # raw alias score: "Parle-G paichi dhabe" (garbled qty) still names Parle-G fully, so it stays as an AMBER line
    return item.qty is None and item.unit is None and not item.size_cues and m.alias_score < config.GREEN_SCORE


def analyse(transcript: str, cat: Catalog | None = None) -> Analysis:
    cat = cat or load_catalog()
    p = parse(transcript)
    lines = [(it, match_item(it, cat)) for it in p.items]
    return Analysis(p, [(it, m) for it, m in lines if not is_chatter(it, m)])


def process_note(conn: sqlite3.Connection, audio_path: Path, retailer_id: str,
                 order_id: int | None = None, filename: str | None = None) -> tuple[int, int]:
    """Transcribe + parse + match one note into an order (new if order_id is None). Never raises on API errors."""
    audio_path = Path(audio_path)
    if order_id is None:
        order_id = db.insert(conn, "orders", retailer_id=retailer_id, status="DRAFT", created_at=db.now())
    note_id = db.insert(
        conn, "voice_notes", order_id=order_id, kind="order", filename=filename or audio_path.name,
        audio_path=str(audio_path), audio_sha=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
        status="PROCESSING", created_at=db.now(),
    )
    t0 = time.perf_counter()
    cat = load_catalog()
    try:
        tr = transcribe(audio_path, keywords=stt_keywords(cat), topic=config.STT_TOPIC)
    except (gnani_client.GnaniError, RuntimeError) as exc:  # RuntimeError: ffmpeg could not decode
        db.update(conn, "voice_notes", note_id, status="STT_FAILED", error=f"{STT_FAILED_MSG}. ({exc})",
                  latency_s=time.perf_counter() - t0)
        db.log_event(conn, "stt_failed", order_id, note_id=note_id, error=str(exc))
        return order_id, note_id

    an = analyse(tr.text, cat)
    start_no = len(db.order_lines(conn, order_id))
    for n, (item, m) in enumerate(an.lines, start=start_no + 1):
        s, e = an.parse.roman.to_orig(item.start, item.end)
        db.insert(
            conn, "order_lines", order_id=order_id, note_id=note_id, line_no=n, product_text=item.product_text,
            sku_id=m.sku_id, qty=m.qty, unit=m.unit, status=m.status, reasons=m.reasons, score=m.score,
            candidates=m.candidates, span_start=s, span_end=e,
        )
    db.update(conn, "voice_notes", note_id, status="PARSED", transcript=tr.text, detected_lang=tr.detected_lang,
              notes_json=an.parse.notes, api_calls=tr.api_calls, latency_s=time.perf_counter() - t0)
    db.log_event(conn, "note_processed", order_id, note_id=note_id, lines=len(an.lines), api_calls=tr.api_calls)
    return order_id, note_id


def clarification_keywords(cat: Catalog, candidates: list[str]) -> list[str]:
    out: list[str] = []
    for c in candidates:
        s = cat.by_id[c]
        for t in (s.brand, s.product, s.variant, s.pack_size):
            if t not in out:
                out.append(t)
    return out


def process_clarification(conn: sqlite3.Connection, line_id: int, audio_path: Path) -> MatchResult | None:
    """Transcribe the retailer's reply and resolve ONE line against its candidate SKUs only.
    Returns the new match (line updated in place), or None if STT failed."""
    audio_path = Path(audio_path)
    line = db.row(conn, "order_lines", line_id)
    assert line is not None, f"no line {line_id}"
    cat = load_catalog()
    note_id = db.insert(
        conn, "voice_notes", order_id=line["order_id"], kind="clarification", line_id=line_id,
        filename=audio_path.name, audio_path=str(audio_path),
        audio_sha=hashlib.sha256(audio_path.read_bytes()).hexdigest(), status="PROCESSING", created_at=db.now(),
    )
    t0 = time.perf_counter()
    try:
        tr = transcribe(audio_path, keywords=clarification_keywords(cat, line["candidates"]), topic=config.STT_TOPIC)
    except (gnani_client.GnaniError, RuntimeError) as exc:
        db.update(conn, "voice_notes", note_id, status="STT_FAILED", error=f"{STT_FAILED_MSG}. ({exc})")
        return None
    m = match_clarification(parse(tr.text).items, line["candidates"], line["qty"], line["unit"], cat)
    db.update(conn, "voice_notes", note_id, status="PARSED", transcript=tr.text, detected_lang=tr.detected_lang,
              api_calls=tr.api_calls, latency_s=time.perf_counter() - t0)
    if m.status == "GREEN" or m.candidates != line["candidates"] or m.qty != line["qty"]:
        db.update(conn, "order_lines", line_id, sku_id=m.sku_id, qty=m.qty, unit=m.unit, status=m.status,
                  reasons=m.reasons, candidates=m.candidates, resolved_by="clarification")
    db.log_event(conn, "clarification", line["order_id"], line_id=line_id, note_id=note_id, status=m.status)
    return m


def _print_order(tr: Transcript, an: Analysis, cat: Catalog) -> None:
    print(f"Transcript ({tr.detected_lang}, {'cache hit' if tr.cached else f'{tr.api_calls} API call(s)'}):")
    print(f"  {tr.text}\n")
    for n in an.parse.notes:
        print(f"NOTE: {n}")
    print(f"{'#':>2}  {'STATUS':6} {'SKU':7} {'PRODUCT':34} {'QTY':>6} {'UNIT':7} {'SCORE':>5}  REASONS / CANDIDATES")
    for i, (item, m) in enumerate(an.lines, 1):
        name = cat.by_id[m.sku_id].label if m.sku_id else f"? {item.product_text!r}"
        qty = "" if m.qty is None else f"{m.qty:g}"
        extra = ", ".join(m.reasons)
        if len(m.candidates) > 1 or m.status == "RED":
            extra += " [" + " | ".join(cat.by_id[c].label for c in m.candidates) + "]"
        print(f"{i:>2}  {m.status:6} {m.sku_id or '-':7} {name[:34]:34} {qty:>6} {m.unit or '':7} {m.score:5.0f}  {extra}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m parchi.pipeline", description="Parse one voice note into an order.")
    ap.add_argument("audio", type=Path)
    ap.add_argument("--no-bias", action="store_true", help="transcribe without catalog keywords/topic")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    cat = load_catalog()
    kw, topic = (None, None) if args.no_bias else (stt_keywords(cat), config.STT_TOPIC)
    try:
        tr = transcribe(args.audio, keywords=kw, topic=topic)
    except (gnani_client.GnaniError, RuntimeError) as exc:
        print(f"STT failed: {exc}", file=sys.stderr)
        return 1
    _print_order(tr, analyse(tr.text, cat), cat)
    u = gnani_client.usage()
    print(f"\nAPI usage: {u['total']}/{config.MAX_API_CALLS} (stt={u.get('stt', 0)}, tts={u.get('tts', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
