"""Catalog loading, alias index and STT keyword list."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from parchi import config
from parchi.translit import phrase_key

_DEVA = re.compile(r"[ऀ-ॿ]")


@dataclass(frozen=True)
class Sku:
    sku_id: str
    brand: str
    product: str
    variant: str
    pack_size: str
    sale_unit: str
    units_per_carton: int
    mrp_inr: float
    aliases: tuple[str, ...]

    @property
    def family(self) -> tuple[str, str]:
        return (self.brand, self.product)

    @property
    def label(self) -> str:
        return f"{self.product} {self.pack_size}"


@dataclass
class Catalog:
    skus: list[Sku]
    by_id: dict[str, Sku] = field(default_factory=dict)
    alias_keys: dict[str, list[str]] = field(default_factory=dict)  # sku_id -> keyed aliases
    family_tokens: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # tokens shared by all variants
    variant_terms: dict[str, set[str]] = field(default_factory=dict)  # sku_id -> tokens unique to this variant

    def family_skus(self, fam: tuple[str, str]) -> list[Sku]:
        return [s for s in self.skus if s.family == fam]


def pack_amount(pack_size: str) -> tuple[float, str] | None:
    """'100g' -> (100, 'mass'); '1kg' -> (1000, 'mass'); '750ml' -> (750, 'vol'); '1L' -> (1000, 'vol')."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\s*", pack_size.lower())
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return {"g": (v, "mass"), "kg": (v * 1000, "mass"), "ml": (v, "vol"), "l": (v * 1000, "vol")}[u]


def _tok(phrase: str) -> set[str]:
    return set(phrase_key(phrase).split())


@lru_cache(maxsize=4)
def load_catalog(path: Path = config.CATALOG_CSV) -> Catalog:
    with open(path, encoding="utf-8", newline="") as f:
        skus = [
            Sku(
                sku_id=r["sku_id"].strip(), brand=r["brand"].strip(), product=r["product"].strip(),
                variant=r["variant"].strip(), pack_size=r["pack_size"].strip(),
                sale_unit=r["sale_unit"].strip().upper(), units_per_carton=int(r["units_per_carton"]),
                mrp_inr=float(r["mrp_inr"]),
                aliases=tuple(a.strip() for a in r["aliases"].split("|") if a.strip()),
            )
            for r in csv.DictReader(f)
        ]
    cat = Catalog(skus=skus, by_id={s.sku_id: s for s in skus})
    for s in skus:
        names = {s.product, *s.aliases}  # brand alone is not an alias: "Colgate" spans product lines
        cat.alias_keys[s.sku_id] = sorted({k for n in names if (k := phrase_key(n))})
    for fam in {s.family for s in skus}:
        members = cat.family_skus(fam)
        alias_sets = [set(m.aliases) | {m.brand, m.product} for m in members]
        common = set.intersection(*alias_sets)
        common_tokens = set().union(*(_tok(a) for a in common))
        cat.family_tokens[fam] = common_tokens
        for m, aset in zip(members, alias_sets):
            own = (aset - common) | {m.variant} if len(members) > 1 else set()
            cat.variant_terms[m.sku_id] = set().union(*(_tok(a) for a in own)) - common_tokens if own else set()
    return cat


def stt_keywords(cat: Catalog, limit: int = 200) -> list[str]:
    """Brands first, then one Devanagari alias per brand, then products, then other aliases."""
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = term.strip()
        if t and t.lower() not in seen and "<|" not in t and "|>" not in t and len(out) < limit:
            seen.add(t.lower())
            out.append(t)

    for s in cat.skus:
        add(s.brand)
    for s in cat.skus:
        deva = [a for a in s.aliases if _DEVA.search(a)]
        if deva:
            add(deva[0])
    for s in cat.skus:
        add(s.product)
    for s in cat.skus:
        for a in s.aliases:
            add(a)
    return out
