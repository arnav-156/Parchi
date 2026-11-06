"""Devanagari → simplified lowercase Latin, keeping a char offset map back to the original.

Uses indic-transliteration (ITRANS) and post-processes it into the casual spelling used in
Hinglish chat ("छोटा" → "chhotaa", "दस" → "das"). `key()` then folds spelling variants
("chhota"/"chota", "paanch"/"panch", "wala"/"vala") into one phonetic key; it is applied to
lexicon/catalog entries and transcript tokens alike, so both sides converge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from indic_transliteration import sanscript

# letters+marks | danda | Devanagari digits — split so offsets stay exact around punctuation
_DEVA_RUN = re.compile(r"[ऀ-ॣ॰-ॿ]+|[।॥]|[०-९]+")

# ITRANS → casual Latin (longest first, case-sensitive)
_ITRANS = {
    ".Dh": "dh", ".D": "d","RRi": "ri", "R^i": "ri", "Ch": "chh", "Th": "th", "Dh": "dh",
    "Sh": "sh", "GY": "gy", ".N": "n", ".n": "n", "~N": "n", "~n": "n", ".h": "", ".a": "",
    "||": ".", "|": ".", "T": "t", "D": "d", "N": "n", "M": "n", "A": "aa", "I": "ii", "U": "uu",
    "H": "h", "x": "ksh", "^": "", "~": "", "{}": "",
}
_ITRANS_RE = re.compile("|".join(re.escape(k) for k in sorted(_ITRANS, key=len, reverse=True)))
_SCHWA = re.compile(r"(?<=[a-z]{1})(?<=[bcdfghjklmnpqrstvwxyz])a\b")


def _deva_word(s: str) -> str:
    it = sanscript.transliterate(s, sanscript.DEVANAGARI, sanscript.ITRANS)
    out = _ITRANS_RE.sub(lambda m: _ITRANS[m.group(0)], it).lower()
    # final inherent-vowel (schwa) deletion: "eka" → "ek", "namaka" → "namak" (not for 2-letter words)
    return re.sub(r"[a-z]+", lambda m: _SCHWA.sub("", m.group(0)) if len(m.group(0)) > 2 else m.group(0), out)


@dataclass(frozen=True)
class Romanised:
    text: str
    orig: tuple[int, ...]  # orig[i] = index in source of roman char i; len == len(text) + 1
    source: str

    def to_orig(self, start: int, end: int) -> tuple[int, int]:
        return self.orig[start], self.orig[end]


def romanise(text: str) -> Romanised:
    parts: list[str] = []
    orig: list[int] = []
    pos = 0

    def plain(a: int, b: int) -> None:
        for j in range(a, b):
            lc = text[j].lower()
            parts.append(lc)
            orig.extend([j] * len(lc))

    for m in _DEVA_RUN.finditer(text):
        plain(pos, m.start())
        r = _deva_word(m.group(0))
        n = m.end() - m.start()
        parts.append(r)
        orig.extend(m.start() + (k * n) // len(r) for k in range(len(r)))
        pos = m.end()
    plain(pos, len(text))
    orig.append(len(text))
    return Romanised("".join(parts), tuple(orig), text)


def key(word: str) -> str:
    """Phonetic key for one token: folds chh/ch, w/v, ee/i, oo/u, doubled letters."""
    if re.fullmatch(r"\d+(?:\.\d+)?", word):
        return word  # keep decimals: "1.5" must not become "15"
    w = re.sub(r"[^a-z0-9]", "", word.lower())
    w = w.replace("w", "v").replace("chh", "ch").replace("ee", "i").replace("oo", "u")
    return re.sub(r"([a-z])\1+", r"\1", w)


TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[a-z]+(?:['-][a-z0-9]+)*|[,.!?;:]")


def tokens(roman: str) -> list[tuple[str, int, int]]:
    """(token, start, end) over romanised text; digits split from letters ("200g" → 200, g)."""
    return [(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(roman)]


def phrase_key(text: str) -> str:
    """Key a whole phrase (any script): romanise, tokenize, key each word."""
    return " ".join(k for t, _, _ in tokens(romanise(text).text) if (k := key(t)))
