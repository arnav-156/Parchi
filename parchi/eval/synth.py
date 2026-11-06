"""Synthetic test set: scripted Hinglish orders from the catalog, spoken by Timbre v2.5.

Scripts are deterministic (seeded). Each note gets a voice (≥3 voices, cycled) and a speed (0.9/1.1).
Writes data/testset/scripts.csv (the text, reusable as a reading script for human recordings),
data/testset/labels.csv (source=synthetic) and data/testset/audio/<note_id>.ogg.
"""
from __future__ import annotations

import csv
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from parchi import config, gnani_client
from parchi.catalog import Catalog, Sku, load_catalog
from parchi.templates import UNIT_WORDS, hindi_qty, spoken_size

VOICES = ("Poorvi", "Deepak", "Nalini", "Roopesh")
SPEEDS = (0.9, 1.1)
QTYS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 25, 30, 50, 1.5, 2.5)
CARTON_QTYS = (1, 2, 3, 5, 1.5, 2.5)
OPENERS = ("Bhaiya,", "Namaste ji,", "Haan bhai,", "")
SEPARATORS = (", ", " aur ", ". ")
ENDINGS = (" bhej dena.", ".", " jaldi bhejna.", " kal tak bhej dena.")
LABEL_FIELDS = ["note_id", "line_no", "sku_id", "qty", "unit", "source"]


def testset_dir() -> Path:
    return config.DATA_DIR / "testset"


@dataclass
class Script:
    note_id: str
    text: str
    voice: str
    speed: float
    lines: list[tuple[str, float, str]] = field(default_factory=list)


def _latin(a: str) -> bool:
    return a.isascii()


def product_phrase(sku: Sku, cat: Catalog, rng: random.Random) -> str:
    """How a retailer names this SKU: a common alias, plus a size or variant word when the brand has variants."""
    fam = cat.family_skus(sku.family)
    common = [a for a in sku.aliases if _latin(a) and all(a in f.aliases for f in fam)]
    others = {a for s in cat.skus if s.brand == sku.brand and s.family != sku.family for a in s.aliases}
    if others:  # brand has several product lines ("Lay's"): name this line specifically
        common = [a for a in common if a not in others] or common
    base = sku.brand if sku.brand.lower() in {a.lower() for a in common} else rng.choice(common or [sku.brand])
    if len(fam) == 1:
        return base
    if len({f.pack_size for f in fam}) == len(fam):
        return f"{base} {spoken_size(sku.pack_size, 'hi-IN')} wala"
    own = [a for a in sku.aliases if _latin(a) and not all(a in f.aliases for f in fam)]
    return rng.choice(own)


def _unit_and_qty(sku: Sku, rng: random.Random) -> tuple[str, float]:
    if sku.sale_unit == "SACK":
        return "SACK", rng.choice((1, 2, 3, 5))
    options = ["CARTON", sku.sale_unit]
    if sku.sale_unit == "PIECE":
        options.append("DOZEN")
    if sku.sale_unit == "PACKET":
        options.append("BOX")
    unit = rng.choice(options)
    qty = rng.choice(CARTON_QTYS if unit == "CARTON" else QTYS)
    if unit == "DOZEN":
        qty = rng.choice((1, 2, 3, 4, 5, 6, 10))
    return unit, qty


def make_scripts(n: int, seed: int = 7, cat: Catalog | None = None, start: int = 1) -> list[Script]:
    cat = cat or load_catalog()
    rng = random.Random(seed)
    scripts = []
    for i in range(start, start + n):
        k = rng.choice((2, 3, 3, 4))
        families = rng.sample(sorted({s.family for s in cat.skus}), k)
        parts, lines = [], []
        for fam in families:
            sku = rng.choice(cat.family_skus(fam))
            unit, qty = _unit_and_qty(sku, rng)
            p, q, u = product_phrase(sku, cat, rng), hindi_qty(qty), UNIT_WORDS["hi-IN"][unit]
            parts.append(rng.choice((f"{p} ke {q} {u}", f"{q} {u} {p}", f"{p} {q} {u}")))
            # expected output: DOZEN of a PIECE item is counted in pieces
            lines.append((sku.sku_id, qty * 12, "PIECE") if unit == "DOZEN" and sku.sale_unit == "PIECE"
                         else (sku.sku_id, qty, unit))
        body = parts[0]
        for part in parts[1:]:
            body += rng.choice(SEPARATORS) + part
        text = " ".join(x for x in (rng.choice(OPENERS), body) if x) + rng.choice(ENDINGS)
        text = text.replace(". .", ".").replace("..", ".")
        scripts.append(Script(f"syn_{i:03d}", text, VOICES[i % len(VOICES)], SPEEDS[i % len(SPEEDS)], lines))
    return scripts


def existing_labels() -> list[dict[str, str]]:
    p = testset_dir() / "labels.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _append_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def pending(scripts: list[Script]) -> list[Script]:
    """Scripts whose audio is not on disk yet (each costs one Timbre call unless TTS-cached)."""
    audio = testset_dir() / "audio"
    return [s for s in scripts if not (audio / f"{s.note_id}.ogg").exists()]


def render(scripts: list[Script]) -> list[Path]:
    """Speak each pending script with Timbre v2.5 and write audio + labels + scripts (idempotent)."""
    out_dir = testset_dir() / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    labelled = {r["note_id"] for r in existing_labels()}
    written = []
    for s in pending(scripts):
        ogg = gnani_client.tts(s.text, voice=s.voice, speed=s.speed, language="hi-IN")
        dst = out_dir / f"{s.note_id}.ogg"
        shutil.copy(ogg, dst)
        written.append(dst)
        if s.note_id not in labelled:
            _append_csv(testset_dir() / "labels.csv", LABEL_FIELDS, [
                {"note_id": s.note_id, "line_no": i, "sku_id": sku, "qty": f"{q:g}", "unit": u, "source": "synthetic"}
                for i, (sku, q, u) in enumerate(s.lines, 1)])
            _append_csv(testset_dir() / "scripts.csv", ["note_id", "voice", "speed", "text"],
                        [{"note_id": s.note_id, "voice": s.voice, "speed": s.speed, "text": s.text}])
    return written


def add_human(script_id: str, audio_path: Path) -> str:
    """Register a teammate's recording of a script: copies audio as hum_<n> with the script's labels, source=human."""
    rows = [r for r in existing_labels() if r["note_id"] == script_id]
    if not rows:
        raise ValueError(f"No labels for script {script_id!r}")
    taken = {r["note_id"] for r in existing_labels()}
    n = 1
    while f"hum_{script_id}_{n}" in taken:
        n += 1
    note_id = f"hum_{script_id}_{n}"
    dst = testset_dir() / "audio" / f"{note_id}{Path(audio_path).suffix.lower()}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(audio_path, dst)
    _append_csv(testset_dir() / "labels.csv", LABEL_FIELDS, [{**r, "note_id": note_id, "source": "human"} for r in rows])
    return note_id
