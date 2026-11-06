"""Reply text templates (hi-IN Hinglish in Latin script, en-IN). No free-form generation.

Numbers in Hinglish replies are rendered as words ("do carton", "sau gram", "ek lakh bees hazaar")
so Timbre never has to guess how to read digits.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache

from parchi import config
from parchi.catalog import Catalog, Sku, pack_amount

SUPPORTED = ("hi-IN", "en-IN")

T = {
    "hi-IN": {
        "confirm": "{name} ji, aapka order mila: {lines}. ",
        "confirm_empty": "{name} ji, aapka order mila. ",
        "variant": "{brand} kaunsa chahiye, {options}?",
        "qty": "{product} kitna bhejna hai?",
        "total": "Anumaanit total {amount} rupaye. ",
        "thanks": "Dhanyavaad, {distributor}.",
        "or": "ya",
    },
    "en-IN": {
        "confirm": "{name}, we have received your order: {lines}. ",
        "confirm_empty": "{name}, we have received your order. ",
        "variant": "Which {brand} do you want, {options}?",
        "qty": "How much {product} should we send?",
        "total": "Estimated total {amount} rupees. ",
        "thanks": "Thank you, {distributor}.",
        "or": "or",
    },
}
UNIT_WORDS = {
    "hi-IN": {"CARTON": "carton", "BOX": "dabba", "PACKET": "packet", "DOZEN": "darjan", "PIECE": "piece",
              "STRIP": "ladi", "KG": "kilo", "G": "gram", "L": "litre", "ML": "ml", "SACK": "bori"},
    "en-IN": {"CARTON": "carton", "BOX": "box", "PACKET": "packet", "DOZEN": "dozen", "PIECE": "piece",
              "STRIP": "strip", "KG": "kg", "G": "grams", "L": "litre", "ML": "ml", "SACK": "sack"},
}
MAX_OPTIONS = 3


@lru_cache(maxsize=1)
def _hi_numbers() -> dict[int, str]:
    with open(config.LEXICON_DIR / "numbers_hi.csv", encoding="utf-8", newline="") as f:
        return {int(r["value"]): r["forms"].split("|")[0] for r in csv.DictReader(f)}


def hindi_int(n: int) -> str:
    """0 ≤ n < 1 crore → Hinglish words, lakh/hazaar/sau grouping: 120500 → 'ek lakh bees hazaar paanch sau'."""
    if n == 0:
        return "shunya"
    w = _hi_numbers()
    parts = []
    for size, name in ((100000, "lakh"), (1000, "hazaar"), (100, "sau")):
        q, n = divmod(n, size)
        if q:
            parts.append(f"{hindi_int(q)} {name}" if q > 1 or name != "sau" else "ek sau")
    if n:
        parts.append(w[n])
    return " ".join(parts)


def hindi_qty(x: float) -> str:
    """Quantity words with fractions: 0.5 aadha, 1.5 dedh, 2.5 dhai, 3.5 saadhe teen, 2.25 sawa do, 4.75 paune paanch."""
    whole, frac = int(x), round(x - int(x), 2)
    if frac == 0:
        return hindi_int(whole)
    special = {0.5: "aadha", 1.5: "dedh", 2.5: "dhai"}
    if x in special:
        return special[x]
    if frac == 0.5:
        return f"saadhe {hindi_int(whole)}"
    if frac == 0.25 and whole >= 1:
        return f"sawa {hindi_int(whole)}"
    if frac == 0.75:
        return f"paune {hindi_int(whole + 1)}"
    return f"{x:g}"


def english_amount(n: int) -> str:
    """Indian grouping in words for big numbers: 125300 → '1 lakh 25 thousand 300'."""
    lakh, rest = divmod(n, 100000)
    thousand, rest = divmod(rest, 1000)
    parts = [f"{lakh} lakh"] * bool(lakh) + [f"{thousand} thousand"] * bool(thousand) + [str(rest)] * bool(rest)
    return " ".join(parts) or "0"


def _num(x: float, lang: str) -> str:
    return hindi_qty(x) if lang == "hi-IN" else f"{x:g}"


def spoken_size(pack: str, lang: str) -> str:
    """'100g' → 'sau gram' / '100 grams'; '1kg' → 'ek kilo'; '750ml' → 'saat sau pachaas ml'."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)", pack.strip().lower())
    if not m:
        return pack
    unit = {"kg": "KG", "g": "G", "ml": "ML", "l": "L"}[m.group(2)]
    return f"{_num(float(m.group(1)), lang)} {UNIT_WORDS[lang][unit]}"


def variant_labels(skus: list[Sku], lang: str) -> list[str]:
    """How to tell sibling variants apart: variant names if they differ, else spoken pack sizes."""
    names = [re.sub(r"\s*\(.*?\)", "", s.variant).strip() for s in skus]
    if len(set(names)) == len(names) and not any(pack_amount(n) for n in names):
        return names
    return [spoken_size(s.pack_size, lang) for s in skus]


def spoken_name(sku: Sku, cat: Catalog, lang: str) -> str:
    """Brand alone if the family has one SKU, else brand + variant ('Colgate do sau gram')."""
    fam = cat.family_skus(sku.family)
    if len(fam) == 1:
        return sku.brand
    return f"{sku.brand} {variant_labels(fam, lang)[fam.index(sku)]}"


def line_text(sku: Sku, qty: float, unit: str, cat: Catalog, lang: str) -> str:
    return f"{spoken_name(sku, cat, lang)} {_num(qty, lang)} {UNIT_WORDS[lang].get(unit, unit.lower())}"


def _options(labels: list[str], lang: str) -> str:
    labels = labels[:MAX_OPTIONS]
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} {T[lang]['or']} {labels[-1]}"


def variant_question(cands: list[Sku], heard: str, lang: str) -> str:
    """'Colgate kaunsa chahiye, sau gram ya do sau gram?' (same family) or product names across families."""
    same_family = len({c.family for c in cands}) == 1
    brands = {c.brand for c in cands}
    brand = brands.pop() if len(brands) == 1 else (heard.title() or cands[0].brand)
    labels = (variant_labels(cands, lang) if same_family
              else [f"{c.product} {spoken_size(c.pack_size, lang)}" for c in cands])
    return T[lang]["variant"].format(brand=brand, options=_options(labels, lang))


def qty_question(product: str, lang: str) -> str:
    return T[lang]["qty"].format(product=product)


@dataclass
class ReplyText:
    text: str
    lang: str
    lang_fallback: bool


def reply_language(detected: str | None) -> tuple[str, bool]:
    """Reply in the detected language if supported, else Hinglish (fallback recorded)."""
    return (detected, False) if detected in SUPPORTED else ("hi-IN", True)


def render_reply(*, name: str, lang: str, lines: list[str], questions: list[str], total: float | None,
                 distributor: str = config.DISTRIBUTOR_NAME) -> str:
    t = T[lang]
    out = t["confirm"].format(name=name, lines=", ".join(lines)) if lines else t["confirm_empty"].format(name=name)
    out += " ".join(questions) + (" " if questions else "")
    if total:
        amt = round(total)
        out += t["total"].format(amount=hindi_int(amt) if lang == "hi-IN" else english_amount(amt))
    return out + t["thanks"].format(distributor=distributor)
