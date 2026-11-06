"""Line items → catalog SKU + GREEN/AMBER/RED status. Pure; no API.

Scoring: rapidfuzz token_set_ratio + partial_ratio over keyed aliases (score `p`), with
token_sort_ratio (`s`) as a tie-breaker so "tata namak" prefers Tata Salt over a generic "namak" alias.
Families (brand+product) scoring within a hair of the best stay as candidates; variant cues
(pack size, "chhota/bada", colour words) then narrow to one SKU or the line is ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from parchi import config
from parchi.catalog import Catalog, Sku, pack_amount
from parchi.parser import LineItem, SizeCue, lexicon
from parchi.translit import key

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
COUNT_UNITS = {"PIECE", "PACKET", "BOX", "DOZEN", "CARTON", "STRIP"}
ALLOWED_UNITS = {
    "PIECE": COUNT_UNITS, "PACKET": COUNT_UNITS, "BOX": COUNT_UNITS, "STRIP": COUNT_UNITS,
    "SACK": {"SACK", "KG"}, "KG": {"KG", "G", "SACK"}, "L": {"L", "ML"},
}
P_TIE, S_TIE = 2.0, 10.0  # families within these margins of the best are treated as tied
MIN_COVERAGE = 60.0  # token_sort below this: the alias explains too little of what was heard
JUNK_SCORE_CAP = 80.0  # ...so the line cannot be GREEN ("Tata Salt thai cotton synthol")


@dataclass
class MatchResult:
    sku_id: str | None
    qty: float | None
    unit: str | None
    status: str
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    candidates: list[str] = field(default_factory=list)


def _phrase(item: LineItem) -> str:
    lx = lexicon()
    ks = (key(w) for w in item.product_words)
    return " ".join(k for k in ks if k and k not in lx.fillers and k not in lx.units and k not in lx.numbers)


def _score(phrase: str, alias: str) -> tuple[float, float]:
    p = fuzz.token_set_ratio(phrase, alias)
    if min(len(phrase), len(alias)) >= 6:  # shorter: partial matches are noise ("jaldi" ~ "haldiram")
        p = max(p, fuzz.partial_ratio(phrase, alias))
    return p, fuzz.token_sort_ratio(phrase, alias)


def _size_ok(sku: Sku, cue: SizeCue) -> bool:
    pa = pack_amount(sku.pack_size)
    if pa is None:
        return False
    amt, dim = pa
    v = cue.value
    return {
        "G": dim == "mass" and amt == v,
        "KG": dim == "mass" and amt == v * 1000,
        "ML": dim == "vol" and amt == v,
        "L": dim == "vol" and amt == v * 1000,
        None: amt in (v, v * 1000),
    }[cue.unit]


def _narrow(cands: list[Sku], item: LineItem, phrase: str, cat: Catalog) -> list[Sku]:
    """Apply size cues, then variant words ("chhota", "neela"), keeping the set if a cue matches nothing."""
    for cue in item.size_cues:
        hit = [c for c in cands if _size_ok(c, cue)]
        if hit:
            cands = hit
    common = set().union(*(cat.family_tokens[c.family] for c in cands))
    cue_words = set(phrase.split()) - common
    if cue_words and len(cands) > 1:
        hits = {c.sku_id: len(cue_words & cat.variant_terms.get(c.sku_id, set())) for c in cands}
        best = max(hits.values())
        if best > 0:
            cands = [c for c in cands if hits[c.sku_id] == best]
    return cands


def _rank(phrase: str, pool: list[Sku], cat: Catalog) -> list[tuple[float, float, Sku]]:
    scored = []
    for s in pool:
        p, r = max((_score(phrase, a) for a in cat.alias_keys[s.sku_id]), default=(0.0, 0.0))
        scored.append((p, r, s))
    return sorted(scored, key=lambda t: (t[0], t[1]), reverse=True)


def _finalise(item: LineItem, cands: list[Sku], score: float, extra_reasons: list[str]) -> MatchResult:
    reasons = list(extra_reasons)
    sku = cands[0] if len(cands) == 1 else None
    if sku is None:
        reasons.append("ambiguous_variant")
    sale_units = {c.sale_unit for c in cands}
    sale_unit = sale_units.pop() if len(sale_units) == 1 else None

    qty, unit = item.qty, item.unit
    if qty is not None and unit is None:
        reasons.append("unit_missing")  # "paanch" alone: 5 packets or 5 cartons? suggest the sale unit, ask
        unit = sale_unit
    if unit == "DOZEN" and sale_unit == "PIECE" and qty is not None:
        qty, unit = qty * 12, "PIECE"
    if score < config.GREEN_SCORE:
        reasons.append("low_confidence")
    if qty is None:
        reasons.append("qty_missing")
    elif qty > config.QTY_SUSPICIOUS:
        reasons.append("qty_suspicious")
    if unit and sale_unit and unit not in ALLOWED_UNITS.get(sale_unit, {sale_unit}):
        reasons.append("unit_mismatch")
    return MatchResult(
        sku_id=sku.sku_id if sku else None, qty=qty, unit=unit,
        status=AMBER if reasons else GREEN, reasons=reasons, score=round(score, 1),
        candidates=[c.sku_id for c in cands],
    )


def match_item(item: LineItem, cat: Catalog) -> MatchResult:
    phrase = _phrase(item)
    ranked = _rank(phrase, cat.skus, cat) if phrase else []
    if not ranked or ranked[0][0] < config.AMBER_SCORE:
        return MatchResult(
            None, item.qty, item.unit, RED, ["unmatched"], round(ranked[0][0], 1) if ranked else 0.0,
            [s.sku_id for _, _, s in ranked[:3]],
        )
    bp, bs, _ = ranked[0]
    score = bp if bs >= MIN_COVERAGE else min(bp, JUNK_SCORE_CAP)
    fams: list[tuple[str, str]] = []
    for p, r, s in ranked:
        if p >= bp - P_TIE and r >= bs - S_TIE and s.family not in fams:
            fams.append(s.family)
    cands = [s for f in fams for s in cat.family_skus(f)]
    return _finalise(item, _narrow(cands, item, phrase, cat), score, [])


def match_clarification(reply_items: list[LineItem], candidates: list[str], qty: float | None,
                        unit: str | None, cat: Catalog) -> MatchResult:
    """Resolve an AMBER line using the retailer's reply, matching ONLY among its candidate SKUs."""
    pool = [cat.by_id[c] for c in candidates if c in cat.by_id]
    merged = LineItem(
        product_words=[w for it in reply_items for w in it.product_words],
        size_cues=[c for it in reply_items for c in it.size_cues],
    )
    reply_qty = next((it for it in reply_items if it.qty is not None), None)
    # only take a quantity from the reply if the line was missing one ("200 wala, aur Good Day paanch")
    merged.qty, merged.unit = (reply_qty.qty, reply_qty.unit) if reply_qty and qty is None else (qty, unit)
    phrase = _phrase(merged)
    cands = pool
    if phrase:
        ranked = _rank(phrase, pool, cat)
        if ranked and ranked[0][0] >= config.AMBER_SCORE:  # reply names something: keep the best-scoring
            bp, bs, _ = ranked[0]
            cands = [s for p, r, s in ranked if p >= bp - P_TIE and r >= bs - S_TIE]
    # candidates were already vetted by the clerk-facing match, so confidence is not re-judged here
    return _finalise(merged, _narrow(cands, merged, phrase, cat), 100.0, [])
