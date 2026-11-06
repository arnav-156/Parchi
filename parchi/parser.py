"""Deterministic transcript → order line items (+ complaint notes). Pure; no API.

Works on the romanised transcript (translit.romanise) and keyed tokens (translit.key), so
Latin and Devanagari codemix forms of the same order parse identically.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from functools import lru_cache

from parchi import config
from rapidfuzz.distance import Levenshtein

from parchi.translit import Romanised, key, phrase_key, romanise, tokens

SEPARATORS = {"aur", "phir", "fir"}
VERBS_BEFORE_DO = {"de", "bhej", "kar", "rakh", "bhijva", "dal", "la", "laga"}  # "bhej do" = send it
WALA = {key(w) for w in ("wala", "wali", "wale")}
MULTIPLIERS = {"sau": 100, "hajar": 1000, "hazar": 1000}
PUNCT = set(",.!?;:")
SIZE_UNITS = {"G", "ML"}  # always a pack-size cue
SIZE_OR_QTY_UNITS = {"KG", "L"}  # size cue if another quantity follows, else the quantity


@dataclass(frozen=True)
class Lexicon:
    numbers: dict[str, float]
    fractions: dict[str, tuple[str, float]]
    units: dict[str, str]
    fillers: frozenset[str]
    note_phrases: tuple[tuple[str, ...], ...]


def _rows(name: str) -> list[dict[str, str]]:
    with open(config.LEXICON_DIR / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _single_key(form: str) -> str | None:
    k = phrase_key(form)
    return k if k and " " not in k else None


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    numbers: dict[str, float] = {}
    for r in _rows("numbers_hi.csv"):
        for form in r["forms"].split("|"):
            if (k := _single_key(form)) and k not in numbers:
                numbers[k] = float(r["value"])
    fractions = {k: (r["kind"], float(r["value"])) for r in _rows("fractions.csv") if (k := _single_key(r["form"]))}
    units = {k: r["unit"] for r in _rows("units.csv") if (k := _single_key(r["form"]))}
    fillers = frozenset(k for r in _rows("fillers.csv") if (k := _single_key(r["form"])))
    notes = tuple(tuple(phrase_key(r["phrase"]).split()) for r in _rows("note_phrases.csv"))
    return Lexicon(numbers, fractions, units, fillers, tuple(sorted(set(notes), key=len, reverse=True)))


@dataclass
class SizeCue:
    value: float
    unit: str | None  # G / KG / ML / L, or None for "200 wala"


@dataclass
class LineItem:
    product_words: list[str] = field(default_factory=list)
    size_cues: list[SizeCue] = field(default_factory=list)
    qty: float | None = None
    unit: str | None = None
    start: int = 0  # span in romanised text
    end: int = 0

    @property
    def product_text(self) -> str:
        return " ".join(self.product_words)


@dataclass
class ParseResult:
    roman: Romanised
    items: list[LineItem]
    notes: list[str]  # complaint notes, in original transcript script


# --- internal element types ---
@dataclass
class _Q:
    qty: float | None
    unit: str | None
    start: int
    end: int
    wala: bool = False


@dataclass
class _W:
    word: str
    start: int
    end: int


def _num(k: str, lx: Lexicon) -> float | None:
    if k.replace(".", "", 1).isdigit():
        return float(k)
    return lx.numbers.get(k)


def _read_qty(keys: list[str], i: int, lx: Lexicon) -> tuple[float, int] | None:
    """Quantity starting at token i → (value, next index), handling fractions and 'do sau'."""
    k = keys[i]
    nxt = keys[i + 1] if i + 1 < len(keys) else None
    if k in lx.fractions:
        kind, v = lx.fractions[k]
        if kind == "abs":
            val, j = v, i + 1
        elif nxt is not None and (n2 := _num(nxt, lx)) is not None:
            val, j = n2 + v, i + 2  # saadhe teen = 3.5, paune do = 1.75
        else:
            val, j = 1 + v, i + 1  # bare sawa = 1.25, paune = 0.75
    else:
        n1 = _num(k, lx)
        if n1 is None:
            return None
        val, j = n1, i + 1
    if j < len(keys) and keys[j] in MULTIPLIERS and val < 100:
        mult = MULTIPLIERS[keys[j]]
        val, j = val * mult, j + 1  # do sau = 200, dedh sau = 150
        if j < len(keys) and keys[j] not in lx.fractions and (rest := _num(keys[j], lx)) is not None and rest < mult:
            val, j = val + rest, j + 1  # saat sau pachaas = 750
    return val, j


def _elements(seg: list[tuple[str, str, int, int]], lx: Lexicon) -> list[_Q | _W]:
    keys = [k for k, _, _, _ in seg]
    els: list[_Q | _W] = []
    i = 0
    while i < len(seg):
        k, raw, s, e = seg[i]
        prev = keys[i - 1] if i else None
        if k == "do" and prev in VERBS_BEFORE_DO:  # "bhej do" / "de do": verb, not 2
            i += 1
            continue
        if (q := _read_qty(keys, i, lx)) is not None:
            val, j = q
            unit = None
            if j < len(seg) and (unit := _unit_after_number(keys[j], lx)):
                j += 1
            wala = j < len(seg) and keys[j] in WALA
            if wala:
                j += 1
            els.append(_Q(val, unit, s, seg[j - 1][3], wala))
            i = j
            continue
        if k in lx.units and len(k) > 1:  # bare unit, no number ("peti Thums Up")
            els.append(_Q(None, lx.units[k], s, e))
        elif k not in lx.fillers and k not in WALA:
            els.append(_W(raw, s, e))
        i += 1
    return els


def _unit_after_number(k: str, lx: Lexicon) -> str | None:
    """Unit word right after a quantity; tolerates a one-letter STT slip ("dhaba", "cartoon") for 4+ letter words."""
    if k in lx.units:
        return lx.units[k]
    if len(k) >= 4 and k not in lx.numbers and k not in lx.fillers:
        near = [u for form, u in lx.units.items() if len(form) >= 4 and Levenshtein.distance(k, form) <= 1]
        if len(set(near)) == 1:
            return near[0]
    return None


def _is_size(els: list[_Q | _W], idx: int) -> bool:
    q = els[idx]
    assert isinstance(q, _Q)
    if q.qty is None:
        return False
    if q.wala or q.unit in SIZE_UNITS:
        return True
    if q.unit in SIZE_OR_QTY_UNITS:
        nxt = els[idx + 1] if idx + 1 < len(els) else None
        return isinstance(nxt, _Q) and nxt.qty is not None
    return False


def _groups(els: list[_Q | _W]) -> list[LineItem | _Q]:
    """Merge words + size cues into product items; leave quantities as _Q."""
    out: list[LineItem | _Q] = []
    for idx, el in enumerate(els):
        last = out[-1] if out else None
        if isinstance(el, _W):
            if isinstance(last, LineItem):
                last.product_words.append(el.word)
                last.end = el.end
            else:
                out.append(LineItem([el.word], start=el.start, end=el.end))
        elif _is_size(els, idx):
            cue = SizeCue(el.qty, el.unit)  # type: ignore[arg-type]
            if isinstance(last, LineItem):
                last.size_cues.append(cue)
                last.end = el.end
            else:
                out.append(LineItem([], [cue], start=el.start, end=el.end))
        else:
            out.append(el)
    return out


def _find_note(keys: list[str], phrases: tuple[tuple[str, ...], ...]) -> list[int]:
    """Indices of tokens belonging to complaint phrases in this segment."""
    hit: list[int] = []
    for ph in phrases:
        n = len(ph)
        for i in range(len(keys) - n + 1):
            if tuple(keys[i : i + n]) == ph and not set(range(i, i + n)) & set(hit):
                hit.extend(range(i, i + n))
    return hit


def _segments(roman: Romanised) -> list[list[tuple[str, str, int, int]]]:
    segs: list[list[tuple[str, str, int, int]]] = [[]]
    for raw, s, e in tokens(roman.text):
        k = key(raw) if raw not in PUNCT else raw
        if raw in PUNCT or k in SEPARATORS:
            if k in SEPARATORS and segs[-1] and segs[-1][-1][0] == "ek":
                segs[-1].pop()  # "ek aur" = "one more", a separator
            segs.append([])
        elif k:
            segs[-1].append((k, raw, s, e))
    return [s for s in segs if s]


def parse(transcript: str) -> ParseResult:
    lx = lexicon()
    roman = romanise(transcript)
    items: list[LineItem] = []
    notes: list[str] = []
    open_item: LineItem | None = None  # most recent product still waiting for a quantity
    open_pending = False  # True if open_item came from a complaint segment (not yet emitted)
    pending_q: _Q | None = None  # quantity with no product yet ("dedh peti, Thums Up")

    for seg in _segments(roman):
        note_idx = _find_note([k for k, _, _, _ in seg], lx.note_phrases)
        is_note = bool(note_idx)
        if is_note:
            s0, e0 = roman.to_orig(seg[0][2], seg[-1][3])
            notes.append(transcript[s0:e0])
            seg = [t for i, t in enumerate(seg) if i not in set(note_idx)]

        seq = _groups(_elements(seg, lx))
        carried = pending_q  # a quantity carried over a comma binds only to this segment's first, qty-less product
        i = 0
        while i < len(seq):
            if i > 0 and carried is not None and pending_q is carried:
                items.append(_orphan(carried))
                pending_q = None
            el = seq[i]
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            if isinstance(el, LineItem) and isinstance(nxt, _Q):  # "Parle-G ke do carton"
                el.qty, el.unit, el.end = nxt.qty, nxt.unit, nxt.end
                items.append(el)
                i += 2
            elif isinstance(el, _Q) and isinstance(nxt, LineItem):  # "dedh peti Thums Up"
                nxt.qty, nxt.unit, nxt.start = el.qty, el.unit, el.start
                items.append(nxt)
                i += 2
            elif isinstance(el, LineItem):  # product, no quantity
                if pending_q is not None and not is_note:  # quantity said before a comma
                    el.qty, el.unit, el.start = pending_q.qty, pending_q.unit, pending_q.start
                    items.append(el)
                    pending_q = open_item = None
                    i += 1
                    continue
                if not is_note:
                    items.append(el)
                open_item, open_pending = el, is_note
                i += 1
                continue
            else:  # orphan quantity → previous product waiting for one, else unmatched line
                assert isinstance(el, _Q)
                if open_item is not None and open_item.qty is None:
                    open_item.qty, open_item.unit = el.qty, el.unit
                    if open_pending:
                        items.append(open_item)
                    open_item = None
                else:
                    if pending_q is not None:
                        items.append(_orphan(pending_q))
                    pending_q = el
                i += 1
                continue
            open_item = None
        if carried is not None and pending_q is carried:
            items.append(_orphan(carried))
            pending_q = None
    if pending_q is not None:
        items.append(_orphan(pending_q))  # never claimed: surfaces as an unmatched (RED) line
    return ParseResult(roman, items, notes)


def _orphan(q: _Q) -> LineItem:
    return LineItem([], qty=q.qty, unit=q.unit, start=q.start, end=q.end)
